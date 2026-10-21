"""商品圖讀取與抓取。金額不從圖片產生。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from prism.api.deps import Principal, principal_from_request, require_role
from prism.commerce.images import ProductImageService
from prism.commerce.service import CommerceError
from prism.raven.compliance import ComplianceError
from prism.raven.images import ImageCrawlError
from prism.raven.proxies import AccessDenied, ProxyPoolExhausted
from prism.runtime import PrismRuntime


class CrawlBody(BaseModel):
    sku_code: str = Field(min_length=1)
    page_url: str = Field(min_length=1)


def register_image_routes(application: FastAPI) -> None:
    def runtime_of(request: Request) -> PrismRuntime:
        return request.app.state.runtime

    @application.get("/api/v1/scenes/{scene_id}/images")
    def list_images(
        scene_id: str,
        sku_code: str,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "viewer")
        try:
            return ProductImageService(runtime_of(request)).list_images(scene_id, sku_code)
        except CommerceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post("/api/v1/scenes/{scene_id}/images")
    def crawl_images(
        scene_id: str,
        body: CrawlBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        try:
            return ProductImageService(runtime_of(request)).crawl(
                scene_id=scene_id,
                sku_code=body.sku_code,
                page_url=body.page_url,
            )
        except CommerceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ComplianceError, AccessDenied) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ImageCrawlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProxyPoolExhausted as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @application.get("/api/v1/product-images/{image_id}")
    def read_image(
        image_id: str,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> Response:
        require_role(principal, "viewer")
        try:
            body, content_type = ProductImageService(runtime_of(request)).read(image_id)
        except CommerceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="商品圖檔不在物件庫") from exc
        return Response(
            content=body,
            media_type=content_type,
            headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"},
        )
