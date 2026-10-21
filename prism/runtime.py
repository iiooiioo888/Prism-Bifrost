"""組裝採集、歸併與研判。命令列與介面共用這條路徑。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from prism.bridge.ingest import IngestReport, IngestService
from prism.clock import ensure_utc
from prism.bridge.objects import ObjectStore
from prism.config import Settings, load_settings
from prism.db.models import ProbeFailure, ProbeHealthRow, ProbeRun
from prism.db.session import init_db, make_engine, session_factory, session_scope
from prism.raven.base import RawItem
from prism.raven.compliance import SitePolicy
from prism.raven.health import classify_health
from prism.watch.service import WatchService
from prism.weave.merge import Assignment, MergeEngine


@dataclass
class IngestOutcome:
    report: IngestReport
    assignments: list[Assignment]


class PrismRuntime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        if self.settings.database_url.startswith("sqlite:///"):
            path = Path(self.settings.database_url.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = make_engine(self.settings.database_url)
        init_db(self.engine)
        self.factory = session_factory(self.engine)
        self.store = ObjectStore(self.settings.object_store)
        self.policy = SitePolicy.from_config(self.settings.site_rules)

    def ingest_items(self, items: list[RawItem], actor: str = "system:ingest", now: datetime | None = None) -> IngestOutcome:
        with session_scope(self.factory) as session:
            bridge = self.settings.bridge
            dedup = bridge.get("dedup", {})
            service = IngestService(
                session,
                self.store,
                self.policy,
                bloom_size=int(dedup.get("url_bloom_size", 10_000_000)),
                near_dup_threshold=float(dedup.get("near_dup_threshold", 0.95)),
            )
            report = service.accept(items, actor=actor)
            merger = MergeEngine(session, self.settings)
            assignments = [merger.assign(mention) for mention in report.stored]
            WatchService(session, self.settings).score_all(now)
            return IngestOutcome(report, assignments)

    def record_probe(
        self,
        *,
        probe_id: str,
        platform: str,
        backend_id: str,
        acquisition_mode: str | None,
        success: bool,
        item_count: int,
        error: str | None,
        now: datetime | None = None,
    ) -> None:
        moment = now or datetime.now(timezone.utc)
        with session_scope(self.factory) as session:
            session.add(
                ProbeRun(
                    probe_id=probe_id,
                    platform=platform,
                    backend_id=backend_id,
                    acquisition_mode=acquisition_mode,
                    success=1 if success else 0,
                    item_count=item_count,
                    error=error,
                    created_at=moment,
                )
            )
            if not success:
                session.add(
                    ProbeFailure(
                        probe_id=probe_id,
                        platform=platform,
                        error=error or "未知失敗",
                        created_at=moment,
                    )
                )
            self._refresh_health(session, probe_id, platform, moment)

    def _refresh_health(self, session: Session, probe_id: str, platform: str, now: datetime) -> None:
        session.flush()
        runs = session.scalars(
            select(ProbeRun).where(ProbeRun.probe_id == probe_id).order_by(ProbeRun.created_at.asc())
        ).all()
        window = [run for run in runs if ensure_utc(run.created_at) >= ensure_utc(now) - timedelta(hours=24)]
        successes = [run for run in window if run.success]
        fallbacks = [run for run in successes if run.acquisition_mode == "fallback"]
        ratio = (len(fallbacks) / len(successes)) if successes else 0.0
        consecutive = 0
        for run in reversed(runs):
            if run.success:
                break
            consecutive += 1
        last_success = next((ensure_utc(run.created_at) for run in reversed(runs) if run.success), None)
        last_success_run = next((run for run in reversed(runs) if run.success), None)
        last_mode = last_success_run.acquisition_mode if last_success_run else None
        latest_failed = bool(runs) and not runs[-1].success
        health = classify_health(
            probe_id=probe_id,
            platform=platform,
            last_success_at=last_success,
            consecutive_failures=consecutive,
            fallback_ratio_24h=ratio,
            mean_lag_seconds=None,
            last_mode=last_mode,
            all_backends_failed=latest_failed and consecutive > 0,
            now=now,
        )
        row = session.get(ProbeHealthRow, probe_id)
        if row is None:
            row = ProbeHealthRow(probe_id=probe_id, platform=platform, updated_at=now)
            session.add(row)
        row.platform = platform
        row.last_success_at = health.last_success_at
        row.consecutive_failures = health.consecutive_failures
        row.fallback_ratio_24h = round(health.fallback_ratio_24h, 4)
        row.mean_lag_seconds = health.mean_lag_seconds
        row.status = health.status
        row.detail = health.detail
        row.updated_at = now

    def configured_probe_status(self) -> list[dict]:
        configured = self.settings.raven.get("probes", {})
        with session_scope(self.factory) as session:
            rows = {row.probe_id: row for row in session.scalars(select(ProbeHealthRow)).all()}
        payload = []
        for probe_id, spec in configured.items():
            row = rows.get(probe_id)
            enabled = bool(spec.get("enabled", False))
            if not enabled:
                payload.append(
                    {
                        "probe_id": probe_id,
                        "platform": probe_id,
                        "status": "down",
                        "detail": spec.get("locked_reason") or "未啟用，不計入成功流量",
                        "last_success_at": None,
                        "consecutive_failures": 0,
                        "fallback_ratio_24h": 0,
                        "mean_lag_seconds": None,
                        "enabled": False,
                    }
                )
                continue
            if row is None:
                payload.append(
                    {
                        "probe_id": probe_id,
                        "platform": probe_id,
                        "status": "down",
                        "detail": "尚未採集，通道未回報",
                        "last_success_at": None,
                        "consecutive_failures": 0,
                        "fallback_ratio_24h": 0,
                        "mean_lag_seconds": None,
                        "enabled": True,
                    }
                )
                continue
            payload.append(
                {
                    "probe_id": row.probe_id,
                    "platform": row.platform,
                    "status": row.status,
                    "detail": row.detail,
                    "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
                    "consecutive_failures": row.consecutive_failures,
                    "fallback_ratio_24h": float(row.fallback_ratio_24h or 0),
                    "mean_lag_seconds": None if row.mean_lag_seconds is None else float(row.mean_lag_seconds),
                    "enabled": True,
                }
            )
        return payload


def run_async(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)
