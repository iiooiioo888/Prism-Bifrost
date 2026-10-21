"""天貓、淘寶、拼多多、京東、轉轉的商品頁。只寫 SKU 報價，不建立規範事件。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from sqlalchemy import func, select

from prism.commerce.money import money, money_text
from prism.commerce.service import CommerceError, CommerceService
from prism.db.models import CanonicalEvent, EventMention
from prism.db.session import session_scope
from prism.raven.compliance import ComplianceError, SitePolicy
from prism.raven.http import fetch_public_bytes
from prism.raven.proxies import (
    AccessDenied,
    BytesTransport,
    ProxyAttempt,
    ProxyPoolConfig,
    ProxyPoolExhausted,
    TransportError,
)
from prism.raven.robots import robots_allows
from prism.runtime import PrismRuntime

PRODUCT_UA = "PrismBot/1.0 (public product pages; no login; no cookies)"
REDIRECTS = frozenset({301, 302, 303, 307, 308})
DENIED = frozenset({401, 403, 429})
TRANSPORT = frozenset({502, 504})
CAPTCHA_MARKERS = ("驗證碼", "验证码", "人機驗證", "人机验证", "captcha")
LOGIN_MARKERS = ("請登入", "請登錄", "请登录", "登录后", "登入後", "登錄後")
_ITEM_ID = re.compile(r"^\d{1,20}$")
_JD_PATH = re.compile(r"/(\d{1,20})\.html$")


class ListingError(ValueError):
    """這次商品頁不能寫成報價。"""

    def __init__(self, message: str, attempts: tuple[ProxyAttempt, ...] = ()) -> None:
        self.attempts = attempts
        super().__init__(message)


@dataclass(frozen=True)
class Marketplace:
    platform_id: str
    label: str
    hosts: tuple[str, ...]


MARKETPLACES: tuple[Marketplace, ...] = (
    Marketplace("tmall", "天貓", ("detail.tmall.com",)),
    Marketplace("taobao", "淘寶", ("item.taobao.com",)),
    Marketplace("pinduoduo", "拼多多", ("mobile.yangkeduo.com",)),
    Marketplace("jd", "京東", ("item.jd.com",)),
    Marketplace("zhuanzhuan", "轉轉", ("m.zhuanzhuan.com", "www.zhuanzhuan.com")),
)


def marketplace_catalog() -> list[dict[str, Any]]:
    return [
        {"platform": item.platform_id, "label": item.label, "hosts": list(item.hosts)}
        for item in MARKETPLACES
    ]


@dataclass(frozen=True)
class ParsedProduct:
    title: str
    list_price: Decimal | None
    price_basis: str
    detail: str


def identify(url: str) -> tuple[Marketplace, str]:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    platform = next((item for item in MARKETPLACES if host in item.hosts), None)
    if platform is None:
        raise ListingError("只接受天貓、淘寶、拼多多、京東、轉轉的商品頁")
    item_id = _item_id(platform.platform_id, parsed)
    if item_id is None:
        raise ListingError("網址裡沒有商品編號")
    return platform, item_id


async def read_product_page(
    url: str,
    *,
    policy: SitePolicy,
    pool: ProxyPoolConfig,
    transport: BytesTransport | None = None,
) -> tuple[Marketplace, str, ParsedProduct, tuple[ProxyAttempt, ...]]:
    platform, item_id = identify(url)
    fetcher = transport or _default_transport
    attempts: list[ProxyAttempt] = []
    await _ensure_robots(url, policy, pool, fetcher, attempts)
    status, body = await _get(url, policy, pool, fetcher, attempts)
    if status in REDIRECTS:
        raise ListingError("回應是重新導向，沒有跟隨", tuple(attempts))
    if status != 200:
        raise ListingError(f"商品頁回應 {status}，沒有保存", tuple(attempts))
    html = body.decode("utf-8", errors="replace")
    if _marked(html, CAPTCHA_MARKERS):
        raise ListingError("頁面是驗證碼，已停止，不寫入價格", tuple(attempts))
    parsed = _parse_product(html)
    if parsed is None:
        if _marked(html, LOGIN_MARKERS):
            raise ListingError("頁面要求登入，沒有公開商品資料，不寫入價格", tuple(attempts))
        raise ListingError("頁面沒有公開商品資料，不寫入價格", tuple(attempts))
    return platform, item_id, parsed, tuple(attempts)


def store_listing(
    runtime: PrismRuntime,
    *,
    scene_id: str,
    url: str,
    platform: Marketplace,
    item_id: str,
    product: ParsedProduct,
    attempts: tuple[ProxyAttempt, ...],
) -> dict[str, Any]:
    scene = runtime.settings.scenes.get(scene_id)
    if scene is None or scene.fusion != "sku_offer":
        raise CommerceError("這個場景不是商品監測")
    before_events, before_mentions = _event_counts(runtime)
    collected = datetime.now(timezone.utc)
    sku_code = f"{platform.platform_id}:{item_id}"
    service = CommerceService(runtime)
    offer_id = service.record_offer(
        sku_code=sku_code,
        sku_title=product.title,
        scene_id=scene_id,
        platform=platform.platform_id,
        source_url=url,
        upstream_timestamp=None,
        collected_at=collected,
        acquisition_mode="live",
        list_price=product.list_price,
        price_basis=product.price_basis,
        match_confidence=Decimal("0.25"),
        page_list_price_dropped=False,
        protection_cause="unparsed",
        inventory_reliability=Decimal("0"),
        after_sales=Decimal("0"),
        adjustments=[],
    )
    if product.list_price is not None and product.price_basis == "guest":
        service.record_observation(
            sku_code=sku_code,
            observed_on=collected.date(),
            list_price=product.list_price,
            source_url=url,
            created_at=collected,
        )
    _record_attempts(runtime, url, attempts)
    after_events, after_mentions = _event_counts(runtime)
    if after_events != before_events or after_mentions != before_mentions:
        raise CommerceError("商品頁不應建立規範事件")
    return {
        "offer_id": offer_id,
        "scene_id": scene_id,
        "platform": platform.platform_id,
        "label": platform.label,
        "sku_code": sku_code,
        "title": product.title,
        "source_url": url,
        "list_price": None if product.list_price is None else money_text(product.list_price),
        "price_basis": product.price_basis,
        "match_confidence": "0.250",
        "stored": True,
        "event_created": False,
        "detail": product.detail,
    }


async def collect_listing(
    runtime: PrismRuntime,
    *,
    scene_id: str,
    url: str,
    transport: BytesTransport | None = None,
) -> dict[str, Any]:
    scene = runtime.settings.scenes.get(scene_id)
    if scene is None or scene.fusion != "sku_offer":
        raise CommerceError("這個場景不是商品監測")
    chosen = transport if transport is not None else getattr(runtime, "listing_transport", None)
    try:
        platform, item_id, product, attempts = await read_product_page(
            url,
            policy=runtime.policy,
            pool=runtime.settings.proxies,
            transport=chosen,
        )
    except (ListingError, ComplianceError, AccessDenied, ProxyPoolExhausted) as exc:
        _record_attempts(runtime, url, tuple(getattr(exc, "attempts", ()) or ()))
        raise
    return store_listing(
        runtime,
        scene_id=scene_id,
        url=url.strip(),
        platform=platform,
        item_id=item_id,
        product=product,
        attempts=attempts,
    )


def _item_id(platform_id: str, parsed: Any) -> str | None:
    query = parse_qs(parsed.query)
    if platform_id in {"tmall", "taobao"}:
        return _digits(_first(query, "id"))
    if platform_id == "pinduoduo":
        return _digits(_first(query, "goods_id"))
    if platform_id == "zhuanzhuan":
        return _digits(_first(query, "infoId") or _first(query, "infoid"))
    if platform_id == "jd":
        match = _JD_PATH.search(parsed.path or "")
        return match.group(1) if match else None
    return None


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


def _digits(value: str | None) -> str | None:
    if value and _ITEM_ID.match(value):
        return value
    return None


def _parse_product(html: str) -> ParsedProduct | None:
    parser = _PageParser()
    parser.feed(html)
    title = ""
    price: Decimal | None = None
    currency = ""
    for block in parser.scripts:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _products(payload):
            if not title and isinstance(node.get("name"), str):
                title = node["name"].strip()
            found, unit_name = _offer_price(node.get("offers"))
            if price is None and found is not None:
                price = found
                currency = unit_name
    if not title:
        title = parser.meta.get("og:title", "").strip()
    if price is None and parser.meta.get("product:price:amount"):
        price = _decimal(parser.meta["product:price:amount"])
        currency = parser.meta.get("product:price:currency", "")
    if not title:
        return None
    if price is not None and currency and currency.upper() not in {"CNY", "RMB", "¥"}:
        return ParsedProduct(title[:200], None, "guest", "標價幣別不是人民幣，不寫入金額。沒有建立規範事件。")
    if price is None and _marked(html, LOGIN_MARKERS):
        return ParsedProduct(title[:200], None, "logged_in", "頁面要求登入才看得到標價，沒有寫入金額。沒有建立規範事件。")
    if price is None:
        return ParsedProduct(title[:200], None, "guest", "頁面沒有公開標價，不寫入金額。沒有建立規範事件。")
    return ParsedProduct(
        title[:200],
        price,
        "guest",
        "已寫入遊客態標價。跨平台匹配置信度 0.250，不能單獨成為可執行價格結論。沒有建立規範事件。",
    )


def _offer_price(offers: Any) -> tuple[Decimal | None, str]:
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None, ""
    raw = offers.get("price")
    if raw in (None, ""):
        raw = offers.get("lowPrice")
    return _decimal(raw), str(offers.get("priceCurrency") or "")


def _decimal(raw: Any) -> Decimal | None:
    if isinstance(raw, bool) or raw in (None, ""):
        return None
    text = str(raw).strip().replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        return None
    return money(text)


def _products(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        kind = value.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "Product" in kinds:
            found.append(value)
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child)

    walk(node)
    return found


def _marked(html: str, markers: tuple[str, ...]) -> bool:
    sample = html[:8192].lower()
    return any(marker.lower() in sample for marker in markers)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.meta: dict[str, str] = {}
        self._capture = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): (value or "") for key, value in attrs}
        if tag == "script" and attr.get("type", "").lower() == "application/ld+json":
            self._capture = True
            self._buf = []
        if tag != "meta":
            return
        key = (attr.get("property") or attr.get("name") or "").lower()
        if key in {"og:title", "product:price:amount", "product:price:currency"} and attr.get("content"):
            self.meta[key] = attr["content"].strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            self.scripts.append("".join(self._buf))
            self._capture = False
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)


async def _default_transport(url: str, proxy_url: str | None) -> tuple[int, bytes]:
    return await fetch_public_bytes(
        url,
        proxy_url,
        accept="text/html,application/xhtml+xml;q=0.9",
        user_agent=PRODUCT_UA,
    )


async def _ensure_robots(
    url: str,
    policy: SitePolicy,
    pool: ProxyPoolConfig,
    transport: BytesTransport,
    attempts: list[ProxyAttempt],
) -> None:
    if not policy.respect_robots(url):
        return
    parsed = urlparse(url)
    robots_url = urlunparse(parsed._replace(path="/robots.txt", params="", query="", fragment=""))
    policy.assert_allowed(robots_url)
    try:
        status, body = await _get(robots_url, policy, pool, transport, attempts)
    except ProxyPoolExhausted as exc:
        raise ListingError("無法讀取 robots.txt，已停止", tuple(attempts)) from exc
    if status == 404:
        text = ""
    elif status != 200:
        raise ListingError(f"無法確認 robots.txt（{status}），已停止", tuple(attempts))
    else:
        text = body.decode("utf-8", errors="replace")
    if not robots_allows(text, parsed.path or "/"):
        error = ComplianceError(f"robots.txt 不允許這個路徑，已拒絕：{url}")
        error.attempts = tuple(attempts)  # type: ignore[attr-defined]
        raise error


async def _get(
    url: str,
    policy: SitePolicy,
    pool: ProxyPoolConfig,
    transport: BytesTransport,
    attempts: list[ProxyAttempt],
) -> tuple[int, bytes]:
    policy.assert_allowed(url)
    targets: list[tuple[str, str | None]]
    if not pool.enabled:
        targets = [("direct", None)]
    else:
        targets = [(item.proxy_id, item.url) for item in pool.proxies]
    for proxy_id, proxy_url in targets:
        try:
            status, body = await transport(url, proxy_url)
        except TransportError as exc:
            attempts.append(ProxyAttempt(proxy_id, "transport_failure", None, str(exc)))
            if not pool.enabled:
                raise ProxyPoolExhausted(f"直連傳輸失敗：{exc}", tuple(attempts)) from exc
            continue
        if status in TRANSPORT:
            attempts.append(ProxyAttempt(proxy_id, "transport_failure", status, "出口傳輸失敗"))
            if not pool.enabled:
                raise ProxyPoolExhausted("直連傳輸失敗", tuple(attempts))
            continue
        if status in DENIED:
            detail = "目標拒絕，已停止且不改用其他 IP" if pool.enabled else "目標拒絕，直連停止"
            attempts.append(ProxyAttempt(proxy_id, "access_denied", status, detail))
            raise AccessDenied(proxy_id, status, detail, tuple(attempts))
        detail = "未使用代理" if proxy_id == "direct" else "出口可用"
        attempts.append(ProxyAttempt(proxy_id, "success", status, detail))
        return status, body
    raise ProxyPoolExhausted("全部出口傳輸中斷", tuple(attempts))


def _event_counts(runtime: PrismRuntime) -> tuple[int, int]:
    with session_scope(runtime.factory) as session:
        events = int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)
        mentions = int(session.scalar(select(func.count()).select_from(EventMention)) or 0)
    return events, mentions


def _record_attempts(runtime: PrismRuntime, url: str, attempts: tuple[ProxyAttempt, ...]) -> None:
    for item in attempts:
        runtime.record_proxy(
            proxy_id=item.proxy_id,
            target_url=url,
            outcome=item.outcome,
            status_code=item.status_code,
            detail=item.detail,
        )
