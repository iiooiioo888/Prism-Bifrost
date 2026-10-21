"""商品圖只抓公開頁上標成商品圖的檔案。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from prism.commerce.images import ProductImageService
from prism.db.models import CanonicalEvent, ProductImage, ProxyRun
from prism.db.session import session_scope
from prism.demo.commerce import seed_commerce
from prism.demo.images import PAGE_URL, _transport, encode_png, seed_product_images
from prism.raven.compliance import ComplianceError, SitePolicy
from prism.raven.http import PayloadTooLarge
from prism.raven.images import ImageCrawlError, crawl_product_images, extract_product_image_urls
from prism.raven.proxies import AccessDenied, ProxyEndpoint, ProxyPoolConfig
from prism.raven.robots import robots_allows

FRONT = "https://catalog.example/images/front.png"
SIDE = "https://catalog.example/images/side.png"
PAGE = "https://catalog.example/products/demo-sku-01"


def _png(color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    return encode_png(1, 1, lambda _x, _y: color)


def _policy() -> SitePolicy:
    return SitePolicy.from_config(
        {
            "defaults": {"allow_fetch": False, "respect_robots": True},
            "blocked_path_fragments": ["/login", "/captcha", "/paywall"],
            "sites": [{"domain": "catalog.example", "allow_fetch": True, "respect_robots": True}],
        }
    )


def _pool(enabled: bool = False) -> ProxyPoolConfig:
    return ProxyPoolConfig(
        enabled=enabled,
        retry_on_access_denied=False,
        transport_failure_limit=3,
        proxies=(
            ProxyEndpoint("local-a", "http://127.0.0.1:8888"),
            ProxyEndpoint("local-b", "http://127.0.0.1:8889"),
        ),
    )


def test_robots_longest_prefix_and_named_agent():
    shared = "User-agent: *\nDisallow: /\nAllow: /images\n"
    assert robots_allows(shared, "/images/a.png")
    assert not robots_allows(shared, "/private")
    named = "User-agent: *\nDisallow: /\n\nUser-agent: PrismBot\nAllow: /catalog\n"
    assert robots_allows(named, "/catalog/x")
    assert not robots_allows(named, "/other", agent="someone-else")


def test_parser_keeps_marked_images_only():
    html = """
    <meta property="og:image" content="/images/front.png">
    <img src="https://catalog.example/ads/banner.png">
    <img itemprop="image" src="https://catalog.example/images/side.png">
    <img src="https://catalog.example/login/pixel.png">
    """
    assert extract_product_image_urls(html, PAGE) == [FRONT, SIDE]


def test_crawl_skips_ads_blocked_paths_and_other_hosts():
    calls: list[str] = []
    front = _png((196, 85, 42))
    side = _png((60, 143, 186))
    html = """
    <meta property="og:image" content="https://catalog.example/images/front.png">
    <img src="https://catalog.example/ads/banner.png">
    <img itemprop="image" src="/images/side.png">
    <img itemprop="image" src="https://cdn.example/a.png">
    <img itemprop="image" src="https://catalog.example/login/hidden.png">
    <img data-product-image="https://catalog.example/images/extra.png">
    """

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        calls.append(url)
        assert proxy is None
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow: /private\n"
        if url == PAGE:
            return 200, html.encode()
        if url == FRONT:
            return 200, front
        if url == SIDE:
            return 200, side
        return 200, b"not-image"

    batch = asyncio.run(crawl_product_images(PAGE, policy=_policy(), pool=_pool(), transport=transport))
    assert [item.image_url for item in batch.images] == [FRONT, SIDE]
    assert all(item.acquisition_mode == "live" for item in batch.images)
    assert not any("/ads/" in url or "/login/" in url or "cdn.example" in url for url in calls)
    reasons = {item.url: item.reason for item in batch.rejected}
    assert "圖片網域未允許" in reasons["https://cdn.example/a.png"]
    assert "login" in reasons["https://catalog.example/login/hidden.png"]
    assert "超過單頁 4 張上限" in reasons["https://catalog.example/images/extra.png"]


def test_robots_disallow_is_not_fetched():
    calls: list[str] = []
    html = '<img data-product-image="https://catalog.example/private/secret.png">'

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        del proxy
        calls.append(url)
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow: /private\n"
        if url == PAGE:
            return 200, html.encode()
        return 200, _png()

    batch = asyncio.run(crawl_product_images(PAGE, policy=_policy(), pool=_pool(), transport=transport))
    assert batch.images == ()
    assert batch.rejected[0].reason == "robots.txt 不允許這個路徑，未抓取"
    assert "https://catalog.example/private/secret.png" not in calls


def test_page_denial_does_not_rotate_or_fetch_images():
    calls: list[tuple[str, str | None]] = []

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        calls.append((url, proxy))
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow:\n"
        return 403, b"forbidden"

    with pytest.raises(AccessDenied, match="沒有換 IP"):
        asyncio.run(
            crawl_product_images(
                PAGE,
                policy=_policy(),
                pool=_pool(True),
                transport=transport,
            )
        )
    page_proxies = [proxy for url, proxy in calls if url == PAGE]
    assert page_proxies == ["http://127.0.0.1:8888"]
    assert all("/images/" not in url for url, _proxy in calls)


def test_captcha_page_stops():
    calls: list[str] = []

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        del proxy
        calls.append(url)
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow:\n"
        return 200, "請完成人機驗證".encode()

    with pytest.raises(AccessDenied):
        asyncio.run(crawl_product_images(PAGE, policy=_policy(), pool=_pool(), transport=transport))
    assert calls == ["https://catalog.example/robots.txt", PAGE]


def test_redirect_is_not_followed():
    calls: list[str] = []

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        del proxy
        calls.append(url)
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow:\n"
        return 302, b""

    with pytest.raises(ImageCrawlError, match="重新導向"):
        asyncio.run(crawl_product_images(PAGE, policy=_policy(), pool=_pool(), transport=transport))
    assert calls == ["https://catalog.example/robots.txt", PAGE]


def test_oversized_image_does_not_try_another_proxy():
    calls: list[str | None] = []
    html = '<meta property="og:image" content="https://catalog.example/images/front.png">'

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        calls.append(proxy)
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow:\n"
        if url == PAGE:
            return 200, html.encode()
        raise PayloadTooLarge("太大")

    batch = asyncio.run(crawl_product_images(PAGE, policy=_policy(), pool=_pool(True), transport=transport))
    assert batch.images == ()
    assert batch.rejected[0].reason == "圖片超過 2MB，未保存"
    image_proxies = calls[2:]
    assert image_proxies == ["http://127.0.0.1:8888"]


def test_direct_image_response_is_stored():
    png = _png()

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        del proxy
        if url.endswith("/robots.txt"):
            return 404, b""
        return 200, png

    batch = asyncio.run(crawl_product_images(FRONT, policy=_policy(), pool=_pool(), transport=transport))
    assert len(batch.images) == 1
    assert batch.images[0].image_url == FRONT
    assert batch.images[0].content_type == "image/png"


def test_service_stores_images_without_creating_events(runtime):
    seed_commerce(runtime)
    with session_scope(runtime.factory) as session:
        before = int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)
    result = ProductImageService(runtime).crawl(
        scene_id="double11",
        sku_code="DEMO-SKU-01",
        page_url=PAGE_URL,
        transport=_transport,
    )
    again = ProductImageService(runtime).crawl(
        scene_id="double11",
        sku_code="DEMO-SKU-01",
        page_url=PAGE_URL,
        transport=_transport,
    )
    with session_scope(runtime.factory) as session:
        after = int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)
        stored = int(session.scalar(select(func.count()).select_from(ProductImage)) or 0)
    assert before == after == 0
    assert stored == 2
    assert len(result["images"]) == 2
    assert len(again["images"]) == 2
    assert {item["image_url"] for item in result["images"]} == {
        "https://catalog.example/images/demo-sku-01.png",
        "https://catalog.example/images/demo-sku-01-side.png",
    }


def test_unlisted_host_never_calls_transport(runtime):
    seed_commerce(runtime)
    called = {"n": 0}

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        del url, proxy
        called["n"] += 1
        return 200, b""

    with pytest.raises(ComplianceError):
        ProductImageService(runtime).crawl(
            scene_id="double11",
            sku_code="DEMO-SKU-01",
            page_url="https://unknown.example/item",
            transport=transport,
        )
    assert called["n"] == 0


def test_denial_is_recorded_and_stores_nothing(runtime):
    seed_commerce(runtime)

    async def transport(url: str, proxy: str | None) -> tuple[int, bytes]:
        del proxy
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow:\n"
        return 403, b"forbidden"

    with pytest.raises(AccessDenied):
        ProductImageService(runtime).crawl(
            scene_id="double11",
            sku_code="DEMO-SKU-01",
            page_url=PAGE,
            transport=transport,
        )
    with session_scope(runtime.factory) as session:
        images = int(session.scalar(select(func.count()).select_from(ProductImage)) or 0)
        denied = session.scalars(select(ProxyRun).where(ProxyRun.outcome == "access_denied")).all()
    assert images == 0
    assert denied
    assert denied[-1].detail == "目標拒絕，直連停止"
    assert denied[-1].proxy_id == "direct"


def test_image_api_lists_bytes_and_refuses_closed_pages(client, runtime):
    seed_commerce(runtime)
    seeded = seed_product_images(runtime)
    assert seeded["images_seeded"] == 1
    assert seed_product_images(runtime)["images_seeded"] == 0
    viewer = {"Authorization": "Bearer dev-viewer"}
    analyst = {"Authorization": "Bearer dev-analyst"}
    listed = client.get("/api/v1/scenes/double11/images?sku_code=DEMO-SKU-01", headers=viewer)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 2
    body = client.get(f"/api/v1/product-images/{items[0]['image_id']}", headers=viewer)
    assert body.status_code == 200
    assert body.content.startswith(b"\x89PNG")
    assert body.headers["content-type"].startswith("image/png")
    assert client.get("/api/v1/scenes/double11/images?sku_code=DEMO-SKU-01").status_code == 401
    assert client.post(
        "/api/v1/scenes/double11/images",
        headers=viewer,
        json={"sku_code": "DEMO-SKU-01", "page_url": PAGE},
    ).status_code == 403
    denied = client.post(
        "/api/v1/scenes/double11/images",
        headers=analyst,
        json={"sku_code": "DEMO-SKU-01", "page_url": "https://unknown.example/item"},
    )
    assert denied.status_code == 403
    harbor = client.post(
        "/api/v1/scenes/harbor_watch/images",
        headers=analyst,
        json={"sku_code": "DEMO-SKU-01", "page_url": PAGE},
    )
    assert harbor.status_code == 404
