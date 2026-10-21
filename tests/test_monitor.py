import asyncio
from urllib.parse import urlparse

import pytest
from sqlalchemy import func, select

from prism.commerce.marketplaces import MARKETPLACES, ListingError
from prism.commerce.monitor import monitor_product, monitor_sku_code
from prism.commerce.service import CommerceError
from prism.db.models import CanonicalEvent, EventMention, ProductMonitor, ProductMonitorHit, SkuOffer
from prism.db.session import session_scope

ROBOTS = b"User-agent: *\nDisallow:\n"
QUERY = "保溫杯"


def _product(name: str, price: str) -> bytes:
    return (
        "<html><head><script type=\"application/ld+json\">"
        f'{{"@type":"Product","name":"{name}","offers":{{"price":"{price}","priceCurrency":"CNY"}}}}'
        "</script></head><body></body></html>"
    ).encode()


def _search(hrefs: list[str], extra: str = "") -> bytes:
    links = "".join(f'<a href="{href}">商品</a>' for href in hrefs)
    return f"<html><body>{extra}{links}</body></html>".encode()


def _routes(pages: dict[str, tuple[int, bytes]], seen: list[tuple[str, str | None]]):
    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        seen.append((url, proxy))
        if urlparse(url).path == "/robots.txt":
            return 200, ROBOTS
        if url not in pages:
            raise AssertionError(url)
        return pages[url]

    return transport


def _item_pages() -> dict[str, tuple[str, str]]:
    return {
        "tmall": ("https://detail.tmall.com/item.htm?id=101", "128.00"),
        "taobao": ("https://item.taobao.com/item.htm?id=202", "88.50"),
        "pinduoduo": ("https://mobile.yangkeduo.com/goods.html?goods_id=303", "66.00"),
        "jd": ("https://item.jd.com/404.html", "199.00"),
        "zhuanzhuan": ("https://m.zhuanzhuan.com/detail?infoId=505", "45.00"),
    }


def _search_pages(items: dict[str, tuple[str, str]] | None = None, overrides: dict[str, tuple[int, bytes]] | None = None):
    items = items or _item_pages()
    pages: dict[str, tuple[int, bytes]] = {}
    for market in MARKETPLACES:
        href, price = items[market.platform_id]
        pages[market.search_url(QUERY)] = (200, _search([href]))
        pages[href] = (200, _product(QUERY, price))
    pages.update(overrides or {})
    return pages


def test_named_product_reads_public_prices_on_all_five_platforms(runtime):
    seen: list[tuple[str, str | None]] = []
    items = _item_pages()
    runtime.listing_transport = _routes(_search_pages(items), seen)
    with session_scope(runtime.factory) as session:
        before_events = session.scalar(select(func.count()).select_from(CanonicalEvent))
        before_mentions = session.scalar(select(func.count()).select_from(EventMention))
    result = asyncio.run(monitor_product(runtime, scene_id="double11", query="  保溫杯  "))
    assert result["query"] == QUERY
    assert result["sku_code"] == monitor_sku_code(QUERY)
    assert result["match_confidence"] == "0.250"
    assert result["event_created"] is False
    assert [item["platform"] for item in result["platforms"]] == [market.platform_id for market in MARKETPLACES]
    assert {item["status"] for item in result["platforms"]} == {"priced"}
    prices = {item["platform"]: item["items"][0]["list_price"] for item in result["platforms"]}
    assert prices == {platform: price for platform, (_url, price) in items.items()}
    requested = [url for url, _proxy in seen]
    for market in MARKETPLACES:
        assert market.search_url(QUERY) in requested
    with session_scope(runtime.factory) as session:
        assert session.scalar(select(func.count()).select_from(CanonicalEvent)) == before_events
        assert session.scalar(select(func.count()).select_from(EventMention)) == before_mentions
        offers = session.scalars(select(SkuOffer)).all()
        assert {offer.platform for offer in offers} == set(items)
        assert {f"{offer.list_price:.2f}" for offer in offers} == {price for _url, price in items.values()}
        assert {f"{offer.match_confidence:.3f}" for offer in offers} == {"0.250"}
        assert len({offer.sku_id for offer in offers}) == 5
        monitor = session.scalar(select(ProductMonitor).where(ProductMonitor.query == QUERY))
        assert monitor is not None
        assert monitor.sku_code == result["sku_code"]
        assert monitor.monitor_id == result["monitor_id"]
    again = asyncio.run(monitor_product(runtime, scene_id="double11", query=QUERY))
    assert again["monitor_id"] == result["monitor_id"]
    assert again["run_id"] != result["run_id"]
    with session_scope(runtime.factory) as session:
        hits = session.scalars(select(ProductMonitorHit)).all()
        assert len(hits) == 10
        assert {hit.run_id for hit in hits} == {result["run_id"], again["run_id"]}


def test_login_captcha_and_denial_stop_that_platform_only(runtime):
    seen: list[tuple[str, str | None]] = []
    items = _item_pages()
    taobao = next(market for market in MARKETPLACES if market.platform_id == "taobao")
    jd = next(market for market in MARKETPLACES if market.platform_id == "jd")
    tmall = next(market for market in MARKETPLACES if market.platform_id == "tmall")
    second = "https://detail.tmall.com/item.htm?id=102"
    pages = _search_pages(items)
    pages[taobao.search_url(QUERY)] = (200, "请登录".encode())
    pages[jd.search_url(QUERY)] = (403, b"no")
    pages[tmall.search_url(QUERY)] = (200, _search([items["tmall"][0], second], extra="验证码"))
    runtime.listing_transport = _routes(pages, seen)
    result = asyncio.run(monitor_product(runtime, scene_id="double11", query=QUERY))
    by_platform = {item["platform"]: item for item in result["platforms"]}
    assert by_platform["taobao"]["status"] == "blocked"
    assert "登入" in by_platform["taobao"]["detail"]
    assert by_platform["jd"]["status"] == "blocked"
    assert by_platform["tmall"]["status"] == "blocked"
    assert "驗證碼" in by_platform["tmall"]["detail"]
    assert by_platform["pinduoduo"]["status"] == "priced"
    assert by_platform["zhuanzhuan"]["status"] == "priced"
    requested = [url for url, _proxy in seen]
    assert not any(urlparse(url).hostname == "item.taobao.com" and urlparse(url).path != "/robots.txt" for url in requested)
    assert not any("item.jd.com" in url for url in requested)
    assert not any("id=101" in url or "id=102" in url for url in requested)
    assert any("goods_id=303" in url for url in requested)


def test_item_denial_does_not_continue_or_change_ip(runtime):
    from dataclasses import replace

    from prism.raven.proxies import ProxyEndpoint, ProxyPoolConfig

    seen: list[tuple[str, str | None]] = []
    items = _item_pages()
    tmall = next(market for market in MARKETPLACES if market.platform_id == "tmall")
    second = "https://detail.tmall.com/item.htm?id=102"
    pages = _search_pages(
        items,
        {tmall.search_url(QUERY): (200, _search([items["tmall"][0], second]))},
    )
    pages[items["tmall"][0]] = (403, b"no")
    pool = ProxyPoolConfig(
        enabled=True,
        retry_on_access_denied=False,
        transport_failure_limit=3,
        proxies=(
            ProxyEndpoint("a", "http://127.0.0.1:8888"),
            ProxyEndpoint("b", "http://127.0.0.1:8889"),
        ),
    )
    runtime.settings = replace(runtime.settings, proxies=pool)
    runtime.listing_transport = _routes(pages, seen)
    result = asyncio.run(monitor_product(runtime, scene_id="double11", query=QUERY))
    tmall_row = next(item for item in result["platforms"] if item["platform"] == "tmall")
    assert tmall_row["status"] == "blocked"
    assert "沒有換 IP" in tmall_row["detail"]
    requested = [url for url, proxy in seen]
    assert second not in requested
    denied = [proxy for url, proxy in seen if url == items["tmall"][0]]
    assert denied == ["http://127.0.0.1:8888"]
    assert "http://127.0.0.1:8889" not in {proxy for _url, proxy in seen}
    assert any(item["status"] == "priced" for item in result["platforms"] if item["platform"] != "tmall")


def test_robots_query_rule_stops_before_the_search_page(runtime):
    seen: list[str] = []

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        seen.append(url)
        if urlparse(url).path != "/robots.txt":
            raise AssertionError(url)
        if "zhuanzhuan.com" in url:
            return 200, b"User-agent: *\nAllow: /\nDisallow: /*?*\n"
        return 200, b"User-agent: *\nDisallow: /\n"

    runtime.listing_transport = transport
    result = asyncio.run(monitor_product(runtime, scene_id="double11", query=QUERY))
    assert {item["status"] for item in result["platforms"]} == {"blocked"}
    zhuanzhuan = next(item for item in result["platforms"] if item["platform"] == "zhuanzhuan")
    assert "robots.txt" in zhuanzhuan["detail"]
    assert not any("keyword=" in url or "/search" in urlparse(url).path for url in seen)


def test_bad_query_and_other_scenes_do_not_fetch(runtime):
    seen: list[tuple[str, str | None]] = []
    runtime.listing_transport = _routes({}, seen)
    with pytest.raises(ListingError, match="商品名稱"):
        asyncio.run(monitor_product(runtime, scene_id="double11", query="   "))
    with pytest.raises(ListingError, match="商品名稱"):
        asyncio.run(monitor_product(runtime, scene_id="double11", query="https://item.jd.com/1.html"))
    with pytest.raises(CommerceError, match="不是商品監測"):
        asyncio.run(monitor_product(runtime, scene_id="harbor_watch", query=QUERY))
    assert seen == []


def test_monitor_api(runtime, client):
    runtime.listing_transport = _routes(_search_pages(), [])
    viewer = {"Authorization": "Bearer dev-viewer"}
    analyst = {"Authorization": "Bearer dev-analyst"}
    body = {"query": QUERY}
    assert client.post("/api/v1/scenes/double11/monitors", json=body, headers=viewer).status_code == 403
    saved = client.post("/api/v1/scenes/double11/monitors", json=body, headers=analyst)
    assert saved.status_code == 200
    payload = saved.json()
    assert payload["event_created"] is False
    assert payload["match_confidence"] == "0.250"
    assert len(payload["platforms"]) == 5
    assert client.post("/api/v1/scenes/harbor_watch/monitors", json=body, headers=analyst).status_code == 404
    refused = client.post(
        "/api/v1/scenes/double11/monitors",
        json={"query": "https://item.jd.com/1.html"},
        headers=analyst,
    )
    assert refused.status_code == 422


SAME = "品牌：虎牌 型號：MMP-T 500ml 黑色 保溫杯"


def test_same_identity_is_one_sku_and_a_different_spec_is_split(runtime):
    items = _item_pages()
    pages = {}
    for market in MARKETPLACES:
        href, price = items[market.platform_id]
        title = SAME if market.platform_id != "jd" else "品牌：虎牌 型號：MMP-T 350ml 黑色 保溫杯"
        pages[market.search_url(SAME)] = (200, _search([href]))
        pages[href] = (200, _product(title, price))
    runtime.listing_transport = _routes(pages, [])
    result = asyncio.run(monitor_product(runtime, scene_id="double11", query=SAME))
    jd = next(item for item in result["platforms"] if item["platform"] == "jd")
    others = [item for item in result["platforms"] if item["platform"] != "jd"]
    assert "分開" in jd["items"][0]["detail"]
    assert {item["items"][0]["match_band"] for item in others} == {"high"}
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["abstain"]["executable"]
    with session_scope(runtime.factory) as session:
        offers = session.scalars(select(SkuOffer)).all()
        high = [offer for offer in offers if f"{offer.match_confidence:.3f}" != "0.000"]
        assert len({offer.sku_id for offer in high}) == 1
        assert any(f"{offer.match_confidence:.3f}" == "0.000" for offer in offers)


def test_daily_recheck_change_confirmation_and_drop_alert(runtime):
    from datetime import datetime, timedelta, timezone

    from prism.commerce.marketplaces import collect_listing
    from prism.commerce.schedule import run_due
    from prism.db.models import ListingWatch, PriceDropAlert, ProductMonitor

    start = datetime(2026, 10, 21, tzinfo=timezone.utc)
    pages = _search_pages()
    seen: list[tuple[str, str | None]] = []
    runtime.listing_transport = _routes(pages, seen)
    asyncio.run(monitor_product(runtime, scene_id="double11", query=QUERY, now=start))
    quiet = asyncio.run(run_due(runtime, now=start + timedelta(hours=2), scene_id="double11"))
    assert quiet["ran"] == 0
    jd_url, jd_price = _item_pages()["jd"]
    pages[jd_url] = (200, _product(QUERY, "100.00"))
    assert jd_price == "199.00"
    due = asyncio.run(run_due(runtime, now=start + timedelta(days=1, minutes=1), scene_id="double11"))
    assert due["ran"] == 1
    assert due["results"][0]["alerts"]
    assert due["results"][0]["alerts"][0]["previous_price"] == "199.00"
    assert due["results"][0]["alerts"][0]["list_price"] == "100.00"
    with session_scope(runtime.factory) as session:
        assert session.scalar(select(func.count()).select_from(PriceDropAlert)) == 1
        monitor = session.scalar(select(ProductMonitor).where(ProductMonitor.query == QUERY))
        assert monitor is not None
        assert monitor.confirm_at is not None
    page = "https://item.jd.com/400.html"
    runtime.listing_transport = _routes({page: (200, _product(QUERY, "199.00"))}, [])
    listing = asyncio.run(collect_listing(runtime, scene_id="double11", url=page, now=start))
    assert listing["schedules"] == ["on_demand", "daily", "on_change"]
    with session_scope(runtime.factory) as session:
        assert session.scalar(select(func.count()).select_from(ListingWatch)) == 1
    held = asyncio.run(run_due(runtime, now=start + timedelta(hours=3), scene_id="double11"))
    assert held["ran"] == 0


def test_official_quote_is_unused_until_configured(runtime):
    seen: list[str] = []

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        seen.append(url)
        if urlparse(url).path != "/robots.txt":
            raise AssertionError(url)
        return 200, b"User-agent: *\nDisallow: /\n"

    runtime.listing_transport = transport
    called = {"n": 0}

    def quotes(query: str) -> list[dict[str, str]]:
        called["n"] += 1
        return [{"url": "https://item.jd.com/404.html", "title": SAME, "price": "88.00"}]

    runtime.official_quotes = quotes
    blocked = asyncio.run(monitor_product(runtime, scene_id="double11", query=SAME))
    assert called["n"] == 0
    assert blocked["official"]["configured"] is False
    from dataclasses import replace

    runtime.settings = replace(runtime.settings, official={"enabled": True})
    saved = asyncio.run(monitor_product(runtime, scene_id="double11", query=SAME))
    assert called["n"] == 1
    assert saved["official"]["configured"] is True
    jd = next(item for item in saved["platforms"] if item["platform"] == "jd")
    assert jd["items"][0]["list_price"] == "88.00"
    assert not any("item.jd.com" in url and not url.endswith("/robots.txt") for url in seen)
