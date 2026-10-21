"""探針健康度。降級必須顯式，禁止把失敗算成零流量的健康。"""

from __future__ import annotations

from datetime import datetime, timedelta

from prism.raven.base import ProbeHealth


def classify_health(
    *,
    probe_id: str,
    platform: str,
    last_success_at: datetime | None,
    consecutive_failures: int,
    fallback_ratio_24h: float,
    mean_lag_seconds: float | None,
    last_mode: str | None,
    all_backends_failed: bool,
    now: datetime,
    stale_after: timedelta = timedelta(hours=6),
) -> ProbeHealth:
    stale = last_success_at is None or now - last_success_at > stale_after
    if last_success_at is None and consecutive_failures >= 3:
        status = "down"
        detail = "連續失敗且尚未成功，通道中斷"
    elif last_success_at is not None and stale and consecutive_failures > 0:
        status = "down"
        detail = "超過健康窗口仍在失敗，通道中斷"
    elif all_backends_failed or last_mode == "fallback" or fallback_ratio_24h > 0 or consecutive_failures > 0:
        status = "degraded"
        detail = "備選路徑啟用，或最近一次採集失敗"
    elif last_success_at is None:
        status = "down"
        detail = "沒有成功採集記錄"
    else:
        status = "healthy"
        detail = "首選路徑正常"
    return ProbeHealth(
        probe_id=probe_id,
        platform=platform,
        last_success_at=last_success_at,
        consecutive_failures=consecutive_failures,
        fallback_ratio_24h=fallback_ratio_24h,
        mean_lag_seconds=mean_lag_seconds,
        status=status,  # type: ignore[arg-type]
        detail=detail,
    )
