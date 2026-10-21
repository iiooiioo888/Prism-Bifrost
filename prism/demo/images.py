"""示範商品圖。圖來自允許網域上的公開頁標記，不連外網。"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime

from sqlalchemy import func, select

from prism.commerce.images import ProductImageService
from prism.db.models import ProductImage, Sku
from prism.db.session import session_scope
from prism.demo.commerce import SKU_CODE
from prism.runtime import PrismRuntime

PAGE_URL = "https://catalog.example/products/demo-sku-01"
FRONT_URL = "https://catalog.example/images/demo-sku-01.png"
SIDE_URL = "https://catalog.example/images/demo-sku-01-side.png"


def seed_product_images(runtime: PrismRuntime, now: datetime | None = None) -> dict[str, int]:
    del now
    with session_scope(runtime.factory) as session:
        sku = session.scalar(select(Sku).where(Sku.code == SKU_CODE))
        if sku is None:
            return {"images_seeded": 0, "images": 0}
        existing = int(
            session.scalar(select(func.count()).select_from(ProductImage).where(ProductImage.sku_id == sku.sku_id)) or 0
        )
    if existing:
        return {"images_seeded": 0, "images": existing}
    ProductImageService(runtime).crawl(
        scene_id="double11",
        sku_code=SKU_CODE,
        page_url=PAGE_URL,
        transport=_transport,
    )
    return {"images_seeded": 1, "images": 2}


def encode_png(width: int, height: int, paint) -> bytes:  # type: ignore[no-untyped-def]
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(paint(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def _paint(primary: tuple[int, int, int]):  # type: ignore[no-untyped-def]
    def paint(x: int, y: int) -> tuple[int, int, int]:
        if 18 <= x <= 78 and 14 <= y <= 82:
            if 28 <= x <= 68 and 24 <= y <= 40:
                return (244, 239, 228)
            return primary
        return (244, 239, 228)

    return paint


_FRONT = encode_png(96, 96, _paint((196, 85, 42)))
_SIDE = encode_png(96, 96, _paint((60, 143, 186)))
_PAGE = """<!doctype html>
<html><head>
<meta property="og:image" content="https://catalog.example/images/demo-sku-01.png">
</head><body>
<img src="https://catalog.example/ads/banner.png" alt="廣告">
<img itemprop="image" src="https://catalog.example/images/demo-sku-01-side.png" alt="側面">
<img src="https://catalog.example/login/pixel.png" alt="不該抓">
</body></html>
""".encode()
_ROBOTS = b"User-agent: *\nDisallow: /private\n"


async def _transport(url: str, proxy: str | None) -> tuple[int, bytes]:
    del proxy
    if url == "https://catalog.example/robots.txt":
        return 200, _ROBOTS
    if url == PAGE_URL:
        return 200, _PAGE
    if url == FRONT_URL:
        return 200, _FRONT
    if url == SIDE_URL:
        return 200, _SIDE
    from prism.raven.proxies import TransportError

    raise TransportError(f"示範傳輸沒有這個網址：{url}")
