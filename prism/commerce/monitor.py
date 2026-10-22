"""依商品名稱到五個平台讀公開標價。身份不同就分開，不建立規範事件。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from prism.clock import utcnow
from prism.commerce.compare import Quote, compare_quotes
from prism.commerce.identity import identity_code, observe_traits, parse_traits
from prism.commerce.marketplaces import (
    LISTINGS_PER_PLATFORM,
    MARKETPLACES,
    CAPTCHA_MARKERS,
    LOGIN_MARKERS,
    REDIRECTS,
    ListingError,
    ParsedProduct,
    identify,
    listing_links,
    open_public_page,
    read_product_page,
    store_listing,
    _marked,
    _record_attempts,
)
from prism.commerce.money import money_text, score_text, unit
from prism.commerce.official import official_quotes, official_report
from prism.commerce.schedule import mark_query_schedule, previous_list_price, remember_drop
from prism.commerce.service import CommerceError, CommerceService
from prism.commerce.sku_match import MatchMark, SkuTraits, align
from prism.db.models import ProductMonitor, ProductMonitorHit, Sku
from prism.db.session import session_scope
from prism.raven.compliance import ComplianceError
from prism.raven.proxies import AccessDenied, BytesTransport, ProxyPoolExhausted
from prism.runtime import PrismRuntime

SCHEDULES = ["on_demand", "daily", "on_change"]
MONITOR_NOTE = (
    "依商品名稱在五個平台查找。品牌、型號、容量或顏色不同會分開監測。"
    "低檔匹配只記入標價表，不能單獨成為可執行結論。沒有建立規範事件。"
    "手動、每天，以及標價變動後都會再查。"
)


def clean_query(text: str) -> str:
    query = " ".join(text.split())
    if not query or len(query) > 80 or any(ord(char) < 32 for char in query):
        raise ListingError("請輸入要監控的商品名稱，最多 80 字")
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
    now: datetime | None = None,
) -> dict[str, Any]:
    scene = runtime.settings.scenes.get(scene_id)
    if scene is None or scene.fusion != "sku_offer":
        raise CommerceError("這個場景不是商品監測")
    name = clean_query(query)
    anchor = parse_traits(name)
    code = identity_code(anchor) or monitor_sku_code(name)
    high = unit(scene.sku_match_high)
    medium = unit(scene.sku_match_medium)
    moment = now or utcnow()
    chosen = transport if transport is not None else getattr(runtime, "listing_transport", None)
    buckets: dict[str, list[dict[str, Any]]] = {item.platform_id: [] for item in MARKETPLACES}
    alerts: list[dict[str, str]] = []
    for market in MARKETPLACES:
        hits, found_alerts = await _scan_market(
            runtime, scene_id, name, anchor, market, chosen, high, medium, moment
        )
        buckets[market.platform_id].extend(hits)
        alerts.extend(found_alerts)
    for quote in official_quotes(runtime, name):
        hit, found_alerts = _store_quote(runtime, scene_id, name, anchor, quote, high, medium, moment)
        if hit is None:
            continue
        buckets.setdefault(hit["platform"], []).append(hit)
        alerts.extend(found_alerts)
    flat = [hit for market in MARKETPLACES for hit in buckets.get(market.platform_id, [])]
    groups = [_group(market.platform_id, market.label, buckets.get(market.platform_id, [])) for market in MARKETPLACES]
    monitor_id, run_id = _save(runtime, scene_id, name, code, flat, moment, confirm=bool(alerts))
    recommendations = _recommend(runtime, scene_id, flat)
    comparison = _comparison(runtime, flat, scene.baseline_days_required)
    _attach_reviews(recommendations, comparison)
    confidences = [unit(hit["match_confidence"]) for hit in flat if hit.get("match_confidence")]
    ceiling = min(confidences) if confidences else unit("0.25")
    return {
        "monitor_id": monitor_id,
        "run_id": run_id,
        "scene_id": scene_id,
        "query": name,
        "sku_code": code,
        "match_confidence": score_text(ceiling),
        "event_created": False,
        "detail": MONITOR_NOTE,
        "schedules": list(SCHEDULES),
        "official": official_report(runtime),
        "alerts": alerts,
        "recommendations": recommendations,
        "comparison": comparison,
        "platforms": groups,
    }


async def _scan_market(
    runtime: PrismRuntime,
    scene_id: str,
    query: str,
    anchor: SkuTraits,
    market: Any,
    transport: BytesTransport | None,
    high: Decimal,
    medium: Decimal,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
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
        return [_hit(market, "blocked", "目標拒絕，已停止，沒有改查其餘商品，也沒有換 IP", None)], []
    except ComplianceError as exc:
        _record_attempts(runtime, search_url, tuple(getattr(exc, "attempts", ()) or ()))
        return [_hit(market, "blocked", str(exc), None)], []
    except ProxyPoolExhausted as exc:
        _record_attempts(runtime, search_url, tuple(getattr(exc, "attempts", ()) or ()))
        return [_hit(market, "blocked", str(exc) or "搜尋頁傳輸失敗", None)], []
    except ListingError as exc:
        _record_attempts(runtime, search_url, tuple(getattr(exc, "attempts", ()) or ()))
        return [_hit(market, "blocked", str(exc), None)], []
    if status in REDIRECTS:
        return [_hit(market, "blocked", "搜尋頁是重新導向，沒有跟隨", search_url)], []
    if status != 200:
        return [_hit(market, "blocked", f"搜尋頁回應 {status}，沒有讀取商品", search_url)], []
    html = body.decode("utf-8", errors="replace")
    if _marked(html, CAPTCHA_MARKERS):
        return [_hit(market, "blocked", "搜尋頁是驗證碼，已停止，不寫入價格", search_url)], []
    links = listing_links(html, search_url, LISTINGS_PER_PLATFORM)
    own = [link for link in links if _platform_of(link) == market.platform_id]
    if not own and _marked(html, LOGIN_MARKERS):
        return [_hit(market, "blocked", "搜尋頁要求登入，沒有公開商品，不寫入價格", search_url)], []
    if not own:
        return [_hit(market, "empty", "這一頁沒有公開商品連結", search_url)], []
    hits: list[dict[str, Any]] = []
    alerts: list[dict[str, str]] = []
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
        hit, found = _persist_product(
            runtime,
            scene_id=scene_id,
            query=query,
            anchor=anchor,
            market=market,
            url=link,
            platform=platform,
            item_id=item_id,
            product=product,
            attempts=attempts,
            high=high,
            medium=medium,
            now=now,
        )
        hits.append(hit)
        alerts.extend(found)
    return hits, alerts


def _store_quote(
    runtime: PrismRuntime,
    scene_id: str,
    query: str,
    anchor: SkuTraits,
    quote: dict[str, Any],
    high: Decimal,
    medium: Decimal,
    now: datetime,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    url = str(quote.get("url") or "").strip()
    title = str(quote.get("title") or query).strip()
    try:
        platform, item_id = identify(url)
        price = unit(str(quote.get("price")))
        from prism.commerce.money import money

        amount = money(price)
    except (ListingError, ArithmeticError, ValueError):
        return None, []
    product = ParsedProduct(title[:200], amount, "guest", "官方介面回傳的遊客態標價。")
    return _persist_product(
        runtime,
        scene_id=scene_id,
        query=query,
        anchor=anchor,
        market=platform,
        url=url,
        platform=platform,
        item_id=item_id,
        product=product,
        attempts=(),
        high=high,
        medium=medium,
        now=now,
    )


def _persist_product(
    runtime: PrismRuntime,
    *,
    scene_id: str,
    query: str,
    anchor: SkuTraits,
    market: Any,
    url: str,
    platform: Any,
    item_id: str,
    product: ParsedProduct,
    attempts: tuple[Any, ...],
    high: Decimal,
    medium: Decimal,
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    observed = observe_traits(product.title, anchor)
    matched = align(anchor, observed, high=high, medium=medium)
    conflict = any("衝突" in reason for reason in matched.reasons)
    if matched.band == "high" and not conflict:
        code = identity_code(anchor) or identity_code(observed) or f"{platform.platform_id}:{item_id}"
        title = query
    elif conflict:
        code = identity_code(observed) or f"{platform.platform_id}:{item_id}"
        title = product.title
    else:
        code = f"{platform.platform_id}:{item_id}"
        title = product.title
    previous = previous_list_price(runtime, code, url)
    dropped = previous is not None and product.list_price is not None and product.list_price < previous
    stored = store_listing(
        runtime,
        scene_id=scene_id,
        url=url,
        platform=platform,
        item_id=item_id,
        product=product,
        attempts=attempts,
        sku_code=code,
        sku_title=title,
        match_confidence=matched.confidence,
        page_list_price_dropped=dropped,
        protection_cause="page_list_price" if dropped else "unparsed",
    )
    detail = _match_detail(matched, conflict)
    alerts: list[dict[str, str]] = []
    if dropped and previous is not None and product.list_price is not None:
        alerts.append(
            remember_drop(
                runtime,
                scene_id=scene_id,
                sku_code=code,
                source_url=url,
                previous=previous,
                current=product.list_price,
                now=now,
            )
        )
        detail = f"{detail}頁面標價下降。"
    status_name = "priced" if stored["list_price"] else "withheld"
    return (
        _hit(
            market,
            status_name,
            detail,
            stored["source_url"],
            title=stored["title"],
            list_price=stored["list_price"],
            sku_code=code,
            match_confidence=stored["match_confidence"],
            match_band=matched.band,
            availability=product.availability,
            shipping_price=None if product.shipping_price is None else money_text(product.shipping_price),
            seller=product.seller,
        ),
        alerts,
    )


def _match_detail(matched: MatchMark, conflict: bool) -> str:
    text = "、".join(matched.reasons) or "沒有可用的 SKU 對照欄位"
    if conflict:
        return f"身份不同，已分開監測。{text}。"
    if matched.band != "high":
        return f"尚未確認是同一件商品。{text}。標價只記入標價表。"
    return f"視為同一件商品。{text}。"


def _platform_of(url: str) -> str | None:
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
    sku_code: str | None = None,
    match_confidence: str | None = None,
    match_band: str | None = None,
    availability: str | None = None,
    shipping_price: str | None = None,
    seller: str | None = None,
) -> dict[str, Any]:
    return {
        "platform": market.platform_id,
        "label": market.label,
        "status": status,
        "detail": detail,
        "source_url": source_url,
        "title": title,
        "list_price": list_price,
        "sku_code": sku_code,
        "match_confidence": match_confidence,
        "match_band": match_band,
        "availability": availability,
        "shipping_price": shipping_price,
        "seller": seller,
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
                "sku_code": item.get("sku_code"),
                "match_confidence": item.get("match_confidence"),
                "match_band": item.get("match_band"),
                "availability": item.get("availability"),
                "shipping_price": item.get("shipping_price"),
                "seller": item.get("seller"),
            }
            for item in hits
            if item["source_url"] or item["title"]
        ],
    }


def _comparison(runtime: PrismRuntime, hits: list[dict[str, Any]], baseline_required: int) -> dict[str, Any]:
    service = CommerceService(runtime)
    quotes: list[Quote] = []
    baselines: dict[str, tuple[int, Decimal | None]] = {}
    for hit in hits:
        if hit.get("status") != "priced" or not hit.get("sku_code") or not hit.get("list_price"):
            continue
        code = str(hit["sku_code"])
        if code not in baselines:
            baselines[code] = service.baseline_snapshot(code)
        quotes.append(
            Quote(
                platform=str(hit["platform"]),
                label=str(hit.get("label") or hit["platform"]),
                source_url=str(hit.get("source_url") or ""),
                title=str(hit.get("title") or ""),
                sku_code=code,
                list_price=unit(str(hit["list_price"])),
                price_basis="guest",
                match_band=str(hit.get("match_band") or "low"),
                match_confidence=unit(str(hit.get("match_confidence") or "0")),
                availability=str(hit.get("availability") or "unknown"),
                shipping_price=None if not hit.get("shipping_price") else unit(str(hit["shipping_price"])),
                seller=hit.get("seller"),
                traits=parse_traits(str(hit.get("title") or "")),
            )
        )
    return compare_quotes(quotes, baselines=baselines, baseline_required=baseline_required)


def _attach_reviews(recommendations: list[dict[str, Any]], comparison: dict[str, Any]) -> None:
    by_code = {item.get("sku_code"): item for item in recommendations}
    for group in comparison.get("same_identity") or []:
        note = group.get("review")
        payload = by_code.get(group.get("sku_code"))
        abstain = payload.get("abstain") if isinstance(payload, dict) else None
        if note and isinstance(abstain, dict):
            abstain.setdefault("confirmations", []).append(note)


def _recommend(runtime: PrismRuntime, scene_id: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    codes = {
        hit["sku_code"]
        for hit in hits
        if hit.get("match_band") == "high" and hit.get("status") == "priced" and hit.get("sku_code")
    }
    service = CommerceService(runtime)
    payloads: list[dict[str, Any]] = []
    for sku_code in sorted(codes):
        payloads.append(service.decide(scene_id=scene_id, sku_code=sku_code, node="onsale", path="fast"))
    return payloads


def _save(
    runtime: PrismRuntime,
    scene_id: str,
    query: str,
    sku_code: str,
    hits: list[dict[str, Any]],
    now: datetime,
    *,
    confirm: bool,
) -> tuple[str, str]:
    from prism.db.models import _uuid

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
        mark_query_schedule(monitor, now, confirm=confirm)
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
                    sku_code=item.get("sku_code"),
                    match_confidence=item.get("match_confidence"),
                    match_band=item.get("match_band"),
                    availability=item.get("availability"),
                    shipping_price=item.get("shipping_price"),
                    seller=item.get("seller"),
                    created_at=now,
                )
            )
        sku = session.scalar(select(Sku).where(Sku.code == sku_code))
        if sku is not None and sku.title != query:
            sku.title = query
        return monitor.monitor_id, run_id
