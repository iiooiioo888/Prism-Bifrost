"""依商品名稱到五個平台讀公開標價。找不到或被拒絕時停在該平台，不建立規範事件。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from prism.commerce.marketplaces import (
    LISTINGS_PER_PLATFORM,
    MARKETPLACES,
    CAPTCHA_MARKERS,
    LOGIN_MARKERS,
    REDIRECTS,
    ListingError,
    listing_links,
    open_public_page,
    read_product_page,
    store_listing,
    _marked,
    _record_attempts,
)
from prism.commerce.service import CommerceError
from prism.db.models import ProductMonitor, ProductMonitorHit, Sku
from prism.db.session import session_scope
from prism.raven.compliance import ComplianceError
from prism.raven.proxies import AccessDenied, BytesTransport, ProxyPoolExhausted
from prism.runtime import PrismRuntime

MATCH_TEXT = "0.250"
MONITOR_NOTE = "依商品名稱在五個平台查找。匹配置信度 0.250，只作價格監測，不能單獨成為可執行結論。沒有建立規範事件。"


def clean_query(text: str) -> str:
    query = " ".join(text.split())
    if not query or len(query) > 40 or any(ord(char) < 32 for char in query):
        raise ListingError("請輸入要監控的商品名稱，最多 40 字")
    if "://" in query:
        raise ListingError("這裡填商品名稱。已知的商品頁網址請另作單頁讀取。")
    return query


def monitor_sku_code(query: str) -> str:
    digest = hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()[:16]
    return f"monitor:{digest}"


async def monitor_product(
    runtime: PrismRuntime,
    *,
    scene_id: str,
    query: str,
    transport: BytesTransport | None = None,
) -> dict[str, Any]:
    scene = runtime.settings.scenes.get(scene_id)
    if scene is None or scene.fusion != "sku_offer":
        raise CommerceError("這個場景不是商品監測")
    name = clean_query(query)
    code = monitor_sku_code(name)
    chosen = transport if transport is not None else getattr(runtime, "listing_transport", None)
    groups = []
    flat: list[dict[str, Any]] = []
    for market in MARKETPLACES:
        hits = await _scan_market(runtime, scene_id, name, code, market, chosen)
        flat.extend(hits)
        groups.append(_group(market.platform_id, market.label, hits))
    monitor_id, run_id = _save(runtime, scene_id, name, code, flat)
    return {
        "monitor_id": monitor_id,
        "run_id": run_id,
        "scene_id": scene_id,
        "query": name,
        "sku_code": code,
        "match_confidence": MATCH_TEXT,
        "event_created": False,
        "detail": MONITOR_NOTE,
        "platforms": groups,
    }


async def _scan_market(
    runtime: PrismRuntime,
    scene_id: str,
    query: str,
    sku_code: str,
    market: Any,
    transport: BytesTransport | None,
) -> list[dict[str, Any]]:
    search_url = market.search_url(query)
    try:
        status, body, _attempts = await open_public_page(
            search_url,
            policy=runtime.policy,
            pool=runtime.settings.proxies,
            transport=transport,
        )
    except AccessDenied as exc:
        _record_attempts(runtime, search_url, tuple(getattr(exc, "attempts", ()) or ()))
        return [_hit(market, "blocked", "目標拒絕，已停止，沒有改查其餘商品，也沒有換 IP", None)]
    except ComplianceError as exc:
        _record_attempts(runtime, search_url, tuple(getattr(exc, "attempts", ()) or ()))
        return [_hit(market, "blocked", str(exc), None)]
    except ProxyPoolExhausted as exc:
        _record_attempts(runtime, search_url, tuple(getattr(exc, "attempts", ()) or ()))
        return [_hit(market, "blocked", str(exc) or "搜尋頁傳輸失敗", None)]
    except ListingError as exc:
        _record_attempts(runtime, search_url, tuple(getattr(exc, "attempts", ()) or ()))
        return [_hit(market, "blocked", str(exc), None)]
    if status in REDIRECTS:
        return [_hit(market, "blocked", "搜尋頁是重新導向，沒有跟隨", search_url)]
    if status != 200:
        return [_hit(market, "blocked", f"搜尋頁回應 {status}，沒有讀取商品", search_url)]
    html = body.decode("utf-8", errors="replace")
    if _marked(html, CAPTCHA_MARKERS):
        return [_hit(market, "blocked", "搜尋頁是驗證碼，已停止，不寫入價格", search_url)]
    links = listing_links(html, search_url, LISTINGS_PER_PLATFORM)
    own = [link for link in links if _platform_of(link) == market.platform_id]
    if not own and _marked(html, LOGIN_MARKERS):
        return [_hit(market, "blocked", "搜尋頁要求登入，沒有公開商品，不寫入價格", search_url)]
    if not own:
        return [_hit(market, "empty", "這一頁沒有公開商品連結", search_url)]
    hits: list[dict[str, Any]] = []
    for link in own:
        try:
            platform, item_id, product, attempts = await read_product_page(
                link,
                policy=runtime.policy,
                pool=runtime.settings.proxies,
                transport=transport,
            )
        except AccessDenied:
            hits.append(_hit(market, "blocked", "商品頁拒絕，已停止，沒有改查其餘商品，也沒有換 IP", link))
            break
        except (ListingError, ComplianceError, ProxyPoolExhausted) as exc:
            hits.append(_hit(market, "withheld", str(exc), link))
            continue
        stored = store_listing(
            runtime,
            scene_id=scene_id,
            url=link,
            platform=platform,
            item_id=item_id,
            product=product,
            attempts=attempts,
            sku_code=sku_code,
            sku_title=query,
        )
        status_name = "priced" if stored["list_price"] else "withheld"
        hits.append(
            _hit(
                market,
                status_name,
                stored["detail"],
                stored["source_url"],
                title=stored["title"],
                list_price=stored["list_price"],
            )
        )
    return hits


def _platform_of(url: str) -> str | None:
    from prism.commerce.marketplaces import identify

    try:
        platform, _item = identify(url)
    except ListingError:
        return None
    return platform.platform_id


def _hit(
    market: Any,
    status: str,
    detail: str,
    source_url: str | None,
    title: str | None = None,
    list_price: str | None = None,
) -> dict[str, Any]:
    return {
        "platform": market.platform_id,
        "label": market.label,
        "status": status,
        "detail": detail,
        "source_url": source_url,
        "title": title,
        "list_price": list_price,
    }


def _group(platform_id: str, label: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    if any(item["status"] == "priced" for item in hits):
        status = "priced"
        detail = MONITOR_NOTE
    elif any(item["status"] == "withheld" for item in hits):
        status = "withheld"
        detail = next(item["detail"] for item in hits if item["status"] == "withheld")
    elif hits:
        status = hits[0]["status"]
        detail = hits[0]["detail"]
    else:
        status = "empty"
        detail = "沒有結果"
    return {
        "platform": platform_id,
        "label": label,
        "status": status,
        "detail": detail,
        "items": [
            {
                "title": item["title"],
                "source_url": item["source_url"],
                "list_price": item["list_price"],
                "status": item["status"],
                "detail": item["detail"],
            }
            for item in hits
            if item["source_url"] or item["title"]
        ],
    }


def _save(
    runtime: PrismRuntime,
    scene_id: str,
    query: str,
    sku_code: str,
    hits: list[dict[str, Any]],
) -> tuple[str, str]:
    from prism.db.models import _uuid

    now = datetime.now(timezone.utc)
    run_id = _uuid()
    with session_scope(runtime.factory) as session:
        monitor = session.scalar(
            select(ProductMonitor).where(ProductMonitor.scene_id == scene_id, ProductMonitor.query == query)
        )
        if monitor is None:
            monitor = ProductMonitor(
                scene_id=scene_id,
                query=query,
                sku_code=sku_code,
                created_at=now,
                updated_at=now,
            )
            session.add(monitor)
            session.flush()
        else:
            monitor.updated_at = now
            monitor.sku_code = sku_code
        for item in hits:
            session.add(
                ProductMonitorHit(
                    monitor_id=monitor.monitor_id,
                    run_id=run_id,
                    platform=item["platform"],
                    status=item["status"],
                    detail=item["detail"],
                    source_url=item["source_url"],
                    title=item["title"],
                    list_price=item["list_price"],
                    created_at=now,
                )
            )
        sku = session.scalar(select(Sku).where(Sku.code == sku_code))
        if sku is not None and sku.title != query:
            sku.title = query
        return monitor.monitor_id, run_id
