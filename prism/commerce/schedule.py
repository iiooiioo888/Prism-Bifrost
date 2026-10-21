"""手動、每天，以及標價變動後再查。下降才寫告警。"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from prism.clock import ensure_utc, utcnow
from prism.commerce.money import money
from prism.db.models import ListingWatch, PriceDropAlert, PriceObservation, ProductMonitor, Sku
from prism.db.session import session_scope
from prism.runtime import PrismRuntime

DAILY = timedelta(days=1)
CONFIRM_AFTER = timedelta(hours=1)


def previous_list_price(runtime: PrismRuntime, sku_code: str, source_url: str) -> Decimal | None:
    with session_scope(runtime.factory) as session:
        sku = session.scalar(select(Sku).where(Sku.code == sku_code))
        if sku is None:
            return None
        row = session.scalar(
            select(PriceObservation)
            .where(PriceObservation.sku_id == sku.sku_id, PriceObservation.source_url == source_url)
            .order_by(PriceObservation.created_at.desc())
        )
        if row is None:
            return None
        return money(row.list_price)


def remember_drop(
    runtime: PrismRuntime,
    *,
    scene_id: str,
    sku_code: str,
    source_url: str,
    previous: Decimal,
    current: Decimal,
    now: datetime,
) -> dict[str, str]:
    with session_scope(runtime.factory) as session:
        session.add(
            PriceDropAlert(
                scene_id=scene_id,
                sku_code=sku_code,
                source_url=source_url,
                previous_price=str(money(previous)),
                list_price=str(money(current)),
                created_at=now,
            )
        )
    return {
        "sku_code": sku_code,
        "source_url": source_url,
        "previous_price": f"{money(previous):.2f}",
        "list_price": f"{money(current):.2f}",
        "detail": "頁面標價下降，已排一次再查。補貼、券與紅包不構成這次告警。",
    }


def mark_query_schedule(monitor: ProductMonitor, now: datetime, *, confirm: bool) -> None:
    monitor.next_daily_at = now + DAILY
    monitor.confirm_at = now + CONFIRM_AFTER if confirm else None
    monitor.updated_at = now


def register_listing(
    runtime: PrismRuntime,
    *,
    scene_id: str,
    source_url: str,
    sku_code: str,
    title: str,
    now: datetime,
    confirm: bool,
) -> None:
    with session_scope(runtime.factory) as session:
        watch = session.scalar(
            select(ListingWatch).where(ListingWatch.scene_id == scene_id, ListingWatch.source_url == source_url)
        )
        if watch is None:
            session.add(
                ListingWatch(
                    scene_id=scene_id,
                    source_url=source_url,
                    sku_code=sku_code,
                    title=title,
                    next_daily_at=now + DAILY,
                    confirm_at=now + CONFIRM_AFTER if confirm else None,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        watch.sku_code = sku_code
        watch.title = title
        watch.next_daily_at = now + DAILY
        watch.confirm_at = now + CONFIRM_AFTER if confirm else None
        watch.updated_at = now


def due_queries(runtime: PrismRuntime, now: datetime) -> list[tuple[str, str]]:
    with session_scope(runtime.factory) as session:
        rows = session.scalars(select(ProductMonitor)).all()
        return [(row.scene_id, row.query) for row in rows if _due(row.next_daily_at, row.confirm_at, now)]


def due_listings(runtime: PrismRuntime, now: datetime) -> list[tuple[str, str]]:
    with session_scope(runtime.factory) as session:
        rows = session.scalars(select(ListingWatch)).all()
        return [(row.scene_id, row.source_url) for row in rows if _due(row.next_daily_at, row.confirm_at, now)]


async def run_due(runtime: PrismRuntime, *, now: datetime | None = None, scene_id: str | None = None) -> dict[str, Any]:
    from prism.commerce.marketplaces import collect_listing
    from prism.commerce.monitor import monitor_product
    from prism.raven.compliance import ComplianceError
    from prism.raven.proxies import AccessDenied, ProxyPoolExhausted
    from prism.commerce.marketplaces import ListingError
    from prism.commerce.service import CommerceError

    moment = now or utcnow()
    results: list[dict[str, Any]] = []
    for due_scene, query in due_queries(runtime, moment):
        if scene_id is not None and due_scene != scene_id:
            continue
        results.append(await monitor_product(runtime, scene_id=due_scene, query=query, now=moment))
    for due_scene, source_url in due_listings(runtime, moment):
        if scene_id is not None and due_scene != scene_id:
            continue
        try:
            results.append(await collect_listing(runtime, scene_id=due_scene, url=source_url, now=moment))
        except (ListingError, CommerceError, ComplianceError, AccessDenied, ProxyPoolExhausted) as exc:
            results.append({"source_url": source_url, "scene_id": due_scene, "event_created": False, "detail": str(exc)})
    return {"ran": len(results), "results": results}


def _due(next_daily_at: datetime | None, confirm_at: datetime | None, now: datetime) -> bool:
    if next_daily_at is None or ensure_utc(next_daily_at) <= now:
        return True
    return confirm_at is not None and ensure_utc(confirm_at) <= now
