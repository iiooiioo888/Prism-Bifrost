"""雙11示範 SKU。各平台頁面是同一決策對象的可選方案，不建立規範事件。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from prism.commerce.service import CommerceService
from prism.db.models import PriceDecision, Sku
from prism.db.session import session_scope
from prism.runtime import PrismRuntime

SKU_CODE = "DEMO-SKU-01"


def seed_commerce(runtime: PrismRuntime, now: datetime | None = None) -> dict[str, int]:
    moment = now or datetime.now(timezone.utc)
    service = CommerceService(runtime)
    with session_scope(runtime.factory) as session:
        existing = session.scalar(select(Sku).where(Sku.code == SKU_CODE))
        decisions = 0
        if existing is not None:
            decisions = int(
                session.scalar(
                    select(func.count()).select_from(PriceDecision).where(PriceDecision.sku_id == existing.sku_id)
                )
                or 0
            )
        if existing is not None and decisions:
            return {"commerce_seeded": 0, "offers": 3}
    _offers(service, moment)
    for offset in (2, 1, 0):
        service.record_observation(
            sku_code=SKU_CODE,
            observed_on=(moment - timedelta(days=offset)).date(),
            list_price=Decimal("200.00"),
            source_url="https://shop-a.example/items/demo-sku-01",
            created_at=moment,
        )
    service.decide(scene_id="double11", sku_code=SKU_CODE, node="onsale", path="fast")
    return {"commerce_seeded": 1, "offers": 3}


def _offers(service: CommerceService, moment: datetime) -> None:
    common = {
        "sku_code": SKU_CODE,
        "sku_title": "示範標準化商品",
        "scene_id": "double11",
        "upstream_timestamp": moment - timedelta(minutes=5),
        "collected_at": moment,
        "acquisition_mode": "live",
        "valid_until": "2026-11-11T23:59:00+08:00",
    }
    service.record_offer(
        **common,
        platform="甲店",
        source_url="https://shop-a.example/items/demo-sku-01",
        list_price=Decimal("200.00"),
        price_basis="guest",
        match_confidence=Decimal("0.96"),
        page_list_price_dropped=False,
        protection_cause="",
        inventory_reliability=Decimal("0.2"),
        after_sales=Decimal("0.3"),
        adjustments=[
            {"layer": "item", "kind": "percent", "value": "0.05", "label": "單品折扣", "exclusive_group": None, "priority": 1},
            {"layer": "store", "kind": "amount", "value": "10.00", "label": "店鋪券", "exclusive_group": None, "priority": 1},
        ],
    )
    service.record_offer(
        **common,
        platform="乙店",
        source_url="https://shop-b.example/items/demo-sku-01",
        list_price=Decimal("250.00"),
        price_basis="guest",
        match_confidence=Decimal("0.92"),
        page_list_price_dropped=True,
        protection_cause="page_list_price",
        inventory_reliability=Decimal("0.95"),
        after_sales=Decimal("0.9"),
        adjustments=[
            {"layer": "item", "kind": "amount", "value": "20.00", "label": "單品券", "exclusive_group": None, "priority": 1},
            {"layer": "platform", "kind": "amount", "value": "10.00", "label": "平台券", "exclusive_group": None, "priority": 1},
            {
                "layer": "national_subsidy",
                "kind": "percent",
                "value": "0.10",
                "label": "國補",
                "exclusive_group": None,
                "priority": 1,
            },
        ],
    )
    service.record_offer(
        **common,
        platform="丙店",
        source_url="https://shop-c.example/items/demo-sku-01",
        list_price=None,
        price_basis="logged_in",
        match_confidence=Decimal("0.60"),
        page_list_price_dropped=False,
        protection_cause="logged_in_price",
        inventory_reliability=Decimal("0.50"),
        after_sales=Decimal("0.50"),
        adjustments=[],
    )
