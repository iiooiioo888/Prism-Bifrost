"""SKU 報價入庫與決策讀寫。報價不是規範事件的提及。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from prism.commerce.baseline import as_date, daily_baseline
from prism.commerce.decide import OfferInput, decide_now, refresh_baseline
from prism.commerce.money import unit
from prism.commerce.rules import Adjustment
from prism.db.models import CanonicalEvent, PriceDecision, PriceObservation, Sku, SkuOffer
from prism.db.session import session_scope
from prism.runtime import PrismRuntime
from prism.scenes.registry import ComplianceError, Scene


class CommerceError(ValueError):
    """場景或報價無法形成決策。"""


class CommerceService:
    def __init__(self, runtime: PrismRuntime) -> None:
        self.runtime = runtime

    def scene(self, scene_id: str) -> Scene:
        found = self.runtime.settings.scenes.get(scene_id)
        if found is None:
            raise CommerceError(f"未知場景：{scene_id}")
        return found

    def record_offer(
        self,
        *,
        sku_code: str,
        sku_title: str,
        scene_id: str,
        platform: str,
        source_url: str,
        upstream_timestamp: datetime,
        collected_at: datetime,
        acquisition_mode: str,
        list_price: Decimal | None,
        price_basis: str,
        match_confidence: Decimal,
        page_list_price_dropped: bool,
        protection_cause: str,
        inventory_reliability: Decimal,
        after_sales: Decimal,
        adjustments: list[dict],
        valid_until: str | None = None,
    ) -> str:
        if price_basis == "logged_in" and list_price is not None:
            raise ComplianceError("不採集登入態價格")
        if price_basis not in {"guest", "logged_in"}:
            raise CommerceError("價格基準只能是遊客態或登入態")
        if acquisition_mode not in {"live", "fallback"}:
            raise CommerceError("採集模式只能是 live 或 fallback")
        with session_scope(self.runtime.factory) as session:
            sku = session.scalar(select(Sku).where(Sku.code == sku_code))
            if sku is None:
                sku = Sku(code=sku_code, title=sku_title, scene_id=scene_id, created_at=collected_at)
                session.add(sku)
                session.flush()
            offer = SkuOffer(
                sku_id=sku.sku_id,
                platform=platform,
                source_url=source_url,
                upstream_timestamp=upstream_timestamp,
                collected_at=collected_at,
                acquisition_mode=acquisition_mode,
                list_price=None if list_price is None else str(list_price),
                price_basis=price_basis,
                match_confidence=str(match_confidence),
                page_list_price_dropped=1 if page_list_price_dropped else 0,
                protection_cause=protection_cause,
                inventory_reliability=str(inventory_reliability),
                after_sales=str(after_sales),
                adjustments=adjustments,
                valid_until=valid_until,
                created_at=collected_at,
            )
            session.add(offer)
            session.flush()
            return offer.offer_id

    def record_observation(
        self,
        *,
        sku_code: str,
        observed_on: date,
        list_price: Decimal,
        source_url: str,
        created_at: datetime,
    ) -> None:
        with session_scope(self.runtime.factory) as session:
            sku = session.scalar(select(Sku).where(Sku.code == sku_code))
            if sku is None:
                raise CommerceError(f"尚無 SKU：{sku_code}")
            existing = session.scalar(
                select(PriceObservation).where(
                    PriceObservation.sku_id == sku.sku_id,
                    PriceObservation.observed_on == observed_on,
                    PriceObservation.source_url == source_url,
                )
            )
            if existing is not None:
                return
            session.add(
                PriceObservation(
                    sku_id=sku.sku_id,
                    observed_on=observed_on,
                    list_price=str(list_price),
                    source_url=source_url,
                    created_at=created_at,
                )
            )

    def baseline_days(self, sku_code: str) -> int:
        days, _median = self.baseline_snapshot(sku_code)
        return days

    def baseline_snapshot(self, sku_code: str) -> tuple[int, Decimal | None]:
        with session_scope(self.runtime.factory) as session:
            sku = session.scalar(select(Sku).where(Sku.code == sku_code))
            if sku is None:
                return 0, None
            rows = session.scalars(select(PriceObservation).where(PriceObservation.sku_id == sku.sku_id)).all()
            observations = [(as_date(row.observed_on), unit(row.list_price)) for row in rows]
        return daily_baseline(observations)

    def decide(self, *, scene_id: str, sku_code: str, node: str, path: str) -> dict:
        scene = self.scene(scene_id)
        if scene.fusion != "sku_offer":
            raise CommerceError("此場景的錨點是規範事件，不走 SKU 決策")
        if node not in scene.nodes:
            raise CommerceError(f"節點不在場景配置內：{node}")
        weights = {key: unit(value) for key, value in (scene.ranking_weights or {}).items()}
        with session_scope(self.runtime.factory) as session:
            sku = session.scalar(select(Sku).where(Sku.code == sku_code, Sku.scene_id == scene_id))
            if sku is None:
                raise CommerceError(f"場景內沒有此 SKU：{sku_code}")
            rows = session.scalars(select(SkuOffer).where(SkuOffer.sku_id == sku.sku_id)).all()
            offers = [_offer_input(row) for row in rows]
            sku_title = sku.title
            sku_id = sku.sku_id
        days, median = self.baseline_snapshot(sku_code)
        runner = decide_now if path == "fast" else refresh_baseline
        payload = runner(
            offers=offers,
            node=node,
            scene_id=scene_id,
            sku_code=sku_code,
            sku_title=sku_title,
            weights=weights,
            baseline_days=days,
            baseline_required=scene.baseline_days_required,
            match_high=unit(scene.sku_match_high),
            match_medium=unit(scene.sku_match_medium),
            daily_prices=[] if median is None else [median],
        )
        moment = datetime.now(timezone.utc)
        with session_scope(self.runtime.factory) as session:
            session.add(
                PriceDecision(
                    sku_id=sku_id,
                    scene_id=scene_id,
                    node=node,
                    path=path,
                    payload=payload,
                    created_at=moment,
                )
            )
        return payload

    def list_decisions(self, scene_id: str) -> list[dict]:
        self.scene(scene_id)
        with session_scope(self.runtime.factory) as session:
            rows = session.scalars(
                select(PriceDecision).where(PriceDecision.scene_id == scene_id).order_by(PriceDecision.created_at.desc())
            ).all()
            return [
                {
                    "decision_id": row.decision_id,
                    "sku_id": row.sku_id,
                    "node": row.node,
                    "path": row.path,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "payload": row.payload,
                }
                for row in rows
            ]

    def event_count(self) -> int:
        with session_scope(self.runtime.factory) as session:
            return int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)


def _offer_input(row: SkuOffer) -> OfferInput:
    adjustments = tuple(_adjustment(item) for item in row.adjustments or [])
    list_price = None if row.list_price is None else unit(row.list_price)
    return OfferInput(
        offer_id=row.offer_id,
        platform=row.platform,
        source_url=row.source_url,
        list_price=list_price,
        price_basis=row.price_basis,
        match_confidence=unit(row.match_confidence),
        page_list_price_dropped=bool(row.page_list_price_dropped),
        protection_cause=row.protection_cause or "",
        inventory_reliability=unit(row.inventory_reliability),
        after_sales=unit(row.after_sales),
        adjustments=adjustments,
        acquisition_mode=row.acquisition_mode,
        valid_until=row.valid_until,
    )


def _adjustment(raw: dict) -> Adjustment:
    return Adjustment(
        layer=str(raw["layer"]),
        kind=str(raw["kind"]),
        value=unit(raw["value"]),
        label=str(raw.get("label") or raw["layer"]),
        exclusive_group=raw.get("exclusive_group"),
        priority=int(raw.get("priority") or 0),
        eligibility=str(raw.get("eligibility") or "confirmed"),
    )
