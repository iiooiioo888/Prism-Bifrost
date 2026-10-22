import asyncio
import json
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from prism.commerce.marketplaces import ListingError, collect_listing, identify
from prism.commerce.service import CommerceError
from prism.db.models import CanonicalEvent, EventMention, SkuOffer
from prism.db.session import session_scope
from prism.raven.compliance import ComplianceError
from prism.raven.proxies import AccessDenied, ProxyEndpoint, ProxyPoolConfig


PAGE = """
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"示範保溫杯","offers":{"price":"199.00","priceCurrency":"CNY"}}
</script>
</head><body>請登錄查看優惠</body></html>
"""


def _routes(mapping: dict[str, tuple[int, bytes]], seen: list[tuple[str, str | None]]):
    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        seen.append((url, proxy))
        if url not in mapping:
            raise AssertionError(url)
        return mapping[url]

    return transport


def test_identity_is_limited_to_the_five_product_hosts():
    assert identify("https://detail.tmall.com/item.htm?id=100")[0].platform_id == "tmall"
    assert identify("https://item.taobao.com/item.htm?id=200")[1] == "200"
    assert identify("https://mobile.yangkeduo.com/goods.html?goods_id=300")[0].label == "拼多多"
    assert identify("https://item.jd.com/400.html")[1] == "400"
    assert identify("https://m.zhuanzhuan.com/detail?infoId=500")[0].label == "轉轉"
    with pytest.raises(ListingError, match="只接受"):
        identify("https://github.com/cli/cli")
    with pytest.raises(ListingError, match="商品編號"):
        identify("https://item.taobao.com/item.htm")


def test_public_price_is_a_product_offer_not_an_event(runtime):
    seen: list[tuple[str, str | None]] = []
    runtime.listing_transport = _routes(
        {
            "https://detail.tmall.com/robots.txt": (404, b""),
            "https://detail.tmall.com/item.htm?id=100": (200, PAGE.encode()),
        },
        seen,
    )
    with session_scope(runtime.factory) as session:
        before_events = session.scalar(select(func.count()).select_from(CanonicalEvent))
        before_mentions = session.scalar(select(func.count()).select_from(EventMention))
    result = asyncio.run(
        collect_listing(runtime, scene_id="double11", url="https://detail.tmall.com/item.htm?id=100")
    )
    assert result["event_created"] is False
    assert result["list_price"] == "199.00"
    assert result["price_basis"] == "guest"
    assert result["sku_code"] == "tmall:100"
    assert result["match_confidence"] == "0.250"
    with session_scope(runtime.factory) as session:
        assert session.scalar(select(func.count()).select_from(CanonicalEvent)) == before_events
        assert session.scalar(select(func.count()).select_from(EventMention)) == before_mentions
        offer = session.scalar(select(SkuOffer).where(SkuOffer.offer_id == result["offer_id"]))
        assert offer is not None
        assert offer.platform == "tmall"
        assert offer.list_price is not None
    assert [url for url, _proxy in seen] == [
        "https://detail.tmall.com/robots.txt",
        "https://detail.tmall.com/item.htm?id=100",
    ]


def test_public_stock_shipping_and_seller_are_stored(runtime):
    offer = {
        "@type": "Offer",
        "price": "80.00",
        "priceCurrency": "CNY",
        "availability": "https://schema.org/InStock",
        "seller": {"@type": "Organization", "name": "虎牌旗艦店"},
        "shippingDetails": {"@type": "OfferShippingDetails", "shippingRate": {"value": "6.00", "currency": "CNY"}},
    }
    page = (
        "<html><head><script type=\"application/ld+json\">"
        + json.dumps({"@type": "Product", "name": "虎牌保溫杯", "offers": offer}, ensure_ascii=False)
        + "</script></head><body></body></html>"
    )
    seen: list[tuple[str, str | None]] = []
    runtime.listing_transport = _routes(
        {
            "https://detail.tmall.com/robots.txt": (404, b""),
            "https://detail.tmall.com/item.htm?id=100": (200, page.encode()),
        },
        seen,
    )
    result = asyncio.run(
        collect_listing(runtime, scene_id="double11", url="https://detail.tmall.com/item.htm?id=100")
    )
    assert result["list_price"] == "80.00"
    assert result["availability"] == "instock"
    assert result["shipping_price"] == "6.00"
    assert result["seller"] == "虎牌旗艦店"
    with session_scope(runtime.factory) as session:
        stored = session.scalar(select(SkuOffer).where(SkuOffer.offer_id == result["offer_id"]))
        assert stored is not None
        assert stored.availability == "instock"
        assert f"{stored.shipping_price:.2f}" == "6.00"
        assert stored.seller == "虎牌旗艦店"


def test_login_wall_and_captcha_store_nothing(runtime):
    seen: list[tuple[str, str | None]] = []
    runtime.listing_transport = _routes(
        {
            "https://item.taobao.com/robots.txt": (200, b"User-agent: *\nDisallow:\n"),
            "https://item.taobao.com/item.htm?id=7": (200, "請登錄".encode()),
        },
        seen,
    )
    with pytest.raises(ListingError, match="要求登入"):
        asyncio.run(collect_listing(runtime, scene_id="double11", url="https://item.taobao.com/item.htm?id=7"))
    runtime.listing_transport = _routes(
        {
            "https://item.jd.com/robots.txt": (200, b"User-agent: *\nDisallow:\n"),
            "https://item.jd.com/8.html": (200, "人機驗證".encode()),
        },
        seen,
    )
    with pytest.raises(ListingError, match="驗證碼"):
        asyncio.run(collect_listing(runtime, scene_id="double11", url="https://item.jd.com/8.html"))
    with session_scope(runtime.factory) as session:
        assert session.scalar(select(func.count()).select_from(SkuOffer)) == 0


def test_denial_does_not_try_the_next_proxy(runtime):
    seen: list[tuple[str, str | None]] = []
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
    runtime.listing_transport = _routes(
        {
            "https://m.zhuanzhuan.com/robots.txt": (200, b"User-agent: *\nDisallow:\n"),
            "https://m.zhuanzhuan.com/detail?infoId=9": (403, b"no"),
        },
        seen,
    )
    with pytest.raises(AccessDenied):
        asyncio.run(
            collect_listing(runtime, scene_id="double11", url="https://m.zhuanzhuan.com/detail?infoId=9")
        )
    assert seen == [
        ("https://m.zhuanzhuan.com/robots.txt", "http://127.0.0.1:8888"),
        ("https://m.zhuanzhuan.com/detail?infoId=9", "http://127.0.0.1:8888"),
    ]


def test_robots_and_other_scenes_do_not_fetch_the_product(runtime):
    seen: list[tuple[str, str | None]] = []
    runtime.listing_transport = _routes(
        {"https://mobile.yangkeduo.com/robots.txt": (200, b"User-agent: *\nDisallow: /\n")},
        seen,
    )
    with pytest.raises(ComplianceError, match="robots.txt"):
        asyncio.run(
            collect_listing(
                runtime,
                scene_id="double11",
                url="https://mobile.yangkeduo.com/goods.html?goods_id=3",
            )
        )
    assert seen == [("https://mobile.yangkeduo.com/robots.txt", None)]
    seen.clear()
    with pytest.raises(CommerceError, match="不是商品監測"):
        asyncio.run(collect_listing(runtime, scene_id="harbor_watch", url="https://item.jd.com/1.html"))
    assert seen == []


def test_listing_api(runtime, client):
    runtime.listing_transport = _routes(
        {
            "https://item.jd.com/robots.txt": (404, b""),
            "https://item.jd.com/400.html": (200, PAGE.encode()),
        },
        [],
    )
    viewer = {"Authorization": "Bearer dev-viewer"}
    analyst = {"Authorization": "Bearer dev-analyst"}
    catalog = client.get("/api/v1/marketplaces", headers=viewer)
    assert catalog.status_code == 200
    labels = [item["label"] for item in catalog.json()["items"]]
    assert labels == ["天貓", "淘寶", "拼多多", "京東", "轉轉"]
    assert client.get("/api/v1/marketplaces").status_code == 401
    body = {"url": "https://item.jd.com/400.html"}
    assert client.post("/api/v1/scenes/double11/listings", json=body, headers=viewer).status_code == 403
    saved = client.post("/api/v1/scenes/double11/listings", json=body, headers=analyst)
    assert saved.status_code == 200
    assert saved.json()["event_created"] is False
    assert saved.json()["platform"] == "jd"
    refused = client.post(
        "/api/v1/scenes/double11/listings",
        json={"url": "https://example.com/item?id=1"},
        headers=analyst,
    )
    assert refused.status_code == 422
    assert client.post("/api/v1/scenes/harbor_watch/listings", json=body, headers=analyst).status_code == 404
