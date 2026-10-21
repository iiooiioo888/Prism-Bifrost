"""場景讀取、商品頁與雙11決策介面。"""

from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from prism.api.deps import Principal, principal_from_request, require_role
from prism.commerce.marketplaces import ListingError, collect_listing, marketplace_catalog
from prism.commerce.service import CommerceError, CommerceService
from prism.raven.compliance import ComplianceError as FetchComplianceError
from prism.raven.proxies import AccessDenied, ProxyPoolExhausted
from prism.runtime import PrismRuntime
from prism.scenes.registry import ComplianceError


class DecideBody(BaseModel):
    sku_code: str = Field(min_length=1)
    node: str = "onsale"
    path: str = "fast"


class ListingBody(BaseModel):
    url: str = Field(min_length=8, max_length=500)


def register_scene_routes(application: FastAPI) -> None:
    def runtime_of(request: Request) -> PrismRuntime:
        return request.app.state.runtime

    @application.get("/api/v1/marketplaces")
    def list_marketplaces(principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        return {"items": marketplace_catalog()}

    @application.post("/api/v1/scenes/{scene_id}/listings")
    def create_listing(
        scene_id: str,
        body: ListingBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        try:
            return asyncio.run(
                collect_listing(runtime_of(request), scene_id=scene_id, url=body.url)
            )
        except CommerceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ListingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (FetchComplianceError, ComplianceError, AccessDenied) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ProxyPoolExhausted as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @application.get("/api/v1/scenes")
    def list_scenes(principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        scenes = runtime_of_from(application).settings.scenes
        return {"items": [scene.to_dict() for scene in scenes.values()]}

    @application.get("/api/v1/scenes/{scene_id}")
    def get_scene(scene_id: str, request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        scene = runtime_of(request).settings.scenes.get(scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="未知場景")
        return scene.to_dict()

    @application.get("/api/v1/scenes/{scene_id}/decisions")
    def list_decisions(scene_id: str, request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        try:
            items = CommerceService(runtime_of(request)).list_decisions(scene_id)
        except CommerceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"items": items}

    @application.post("/api/v1/scenes/{scene_id}/decide")
    def decide(
        scene_id: str,
        body: DecideBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        if body.path not in {"fast", "slow"}:
            raise HTTPException(status_code=422, detail="路徑只能是 fast 或 slow")
        try:
            return CommerceService(runtime_of(request)).decide(
                scene_id=scene_id,
                sku_code=body.sku_code,
                node=body.node,
                path=body.path,
            )
        except ComplianceError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except CommerceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def runtime_of_from(application: FastAPI) -> PrismRuntime:
    return application.state.runtime
