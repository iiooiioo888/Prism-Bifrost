"""商品圖入庫。圖是 SKU 的公開事實，不進規範事件，也不產生價格。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select

from prism.clock import ensure_utc
from prism.commerce.service import CommerceError
from prism.db.models import ProductImage, Sku
from prism.db.session import session_scope
from prism.raven.compliance import ComplianceError
from prism.raven.http import fetch_public_bytes
from prism.raven.images import CrawlBatch, ImageCrawlError, crawl_product_images
from prism.raven.proxies import AccessDenied, ProxyPoolExhausted
from prism.runtime import PrismRuntime

BytesTransport = Callable[[str, str | None], Awaitable[tuple[int, bytes]]]


class ProductImageService:
    def __init__(self, runtime: PrismRuntime) -> None:
        self.runtime = runtime

    def list_images(self, scene_id: str, sku_code: str) -> dict:
        self._scene(scene_id)
        with session_scope(self.runtime.factory) as session:
            sku = session.scalar(select(Sku).where(Sku.code == sku_code))
            if sku is None or sku.scene_id != scene_id:
                return {"sku_code": sku_code, "items": []}
            rows = session.scalars(
                select(ProductImage).where(ProductImage.sku_id == sku.sku_id).order_by(ProductImage.created_at.asc())
            ).all()
            return {"sku_code": sku_code, "items": [_public(row, sku_code) for row in rows]}

    def read(self, image_id: str) -> tuple[bytes, str]:
        with session_scope(self.runtime.factory) as session:
            row = session.get(ProductImage, image_id)
            if row is None:
                raise CommerceError("沒有這張商品圖")
            key = row.object_key
            content_type = row.content_type
        return self.runtime.store.get(key), content_type

    def crawl(
        self,
        *,
        scene_id: str,
        sku_code: str,
        page_url: str,
        transport: BytesTransport | None = None,
    ) -> dict:
        scene = self._scene(scene_id)
        if scene.fusion != "sku_offer":
            raise CommerceError("這個場景不保存商品圖")
        sku_id = self._sku_id(scene_id, sku_code)
        chosen = transport or fetch_public_bytes
        failures = {
            item["proxy_id"]: int(item["consecutive_transport_failures"])
            for item in self.runtime.configured_proxy_status()["items"]
        }
        attempts: tuple = ()
        try:
            batch = asyncio.run(
                crawl_product_images(
                    page_url,
                    policy=self.runtime.policy,
                    pool=self.runtime.settings.proxies,
                    transport=chosen,
                    transport_failures=failures,
                )
            )
            attempts = batch.attempts
            images = self._persist(sku_id, sku_code, batch)
            return {
                "sku_code": sku_code,
                "page_url": batch.page_url,
                "note": batch.note,
                "images": images,
                "rejected": [{"url": item.url, "reason": item.reason} for item in batch.rejected],
            }
        except (AccessDenied, ImageCrawlError, ProxyPoolExhausted, ComplianceError) as exc:
            attempts = tuple(getattr(exc, "attempts", ()) or ())
            raise
        finally:
            for item in attempts:
                self.runtime.record_proxy(
                    proxy_id=item.proxy_id,
                    target_url=page_url,
                    outcome=item.outcome,
                    status_code=item.status_code,
                    detail=item.detail,
                )

    def _persist(self, sku_id: str, sku_code: str, batch: CrawlBatch) -> list[dict]:
        moment = datetime.now(timezone.utc)
        payload: list[dict] = []
        with session_scope(self.runtime.factory) as session:
            for image in batch.images:
                row = session.scalar(
                    select(ProductImage).where(
                        ProductImage.sku_id == sku_id,
                        ProductImage.image_url == image.image_url,
                    )
                )
                if row is not None and row.sha256 == image.sha256:
                    payload.append(_public(row, sku_code))
                    continue
                key = self.runtime.store.put(image.body, hint="image")
                if row is None:
                    row = ProductImage(
                        sku_id=sku_id,
                        source_page_url=image.source_page_url,
                        image_url=image.image_url,
                        content_type=image.content_type,
                        byte_size=len(image.body),
                        sha256=image.sha256,
                        object_key=key,
                        acquisition_mode=image.acquisition_mode,
                        collected_at=moment,
                        created_at=moment,
                    )
                    session.add(row)
                else:
                    row.source_page_url = image.source_page_url
                    row.content_type = image.content_type
                    row.byte_size = len(image.body)
                    row.sha256 = image.sha256
                    row.object_key = key
                    row.acquisition_mode = image.acquisition_mode
                    row.collected_at = moment
                session.flush()
                payload.append(_public(row, sku_code))
        return payload

    def _scene(self, scene_id: str):
        scene = self.runtime.settings.scenes.get(scene_id)
        if scene is None:
            raise CommerceError(f"未知場景：{scene_id}")
        return scene

    def _sku_id(self, scene_id: str, sku_code: str) -> str:
        with session_scope(self.runtime.factory) as session:
            sku = session.scalar(select(Sku).where(Sku.code == sku_code))
            if sku is None or sku.scene_id != scene_id:
                raise CommerceError(f"尚無 SKU：{sku_code}")
            return sku.sku_id


def _public(row: ProductImage, sku_code: str) -> dict:
    collected = ensure_utc(row.collected_at) if row.collected_at else None
    return {
        "image_id": row.image_id,
        "sku_code": sku_code,
        "source_page_url": row.source_page_url,
        "image_url": row.image_url,
        "content_type": row.content_type,
        "byte_size": row.byte_size,
        "sha256": row.sha256,
        "acquisition_mode": row.acquisition_mode,
        "collected_at": collected.isoformat() if collected else None,
    }
