"""需登入態或外部金鑰的探針。第一階段只報告未啟用，不進行採集。"""

from __future__ import annotations

from datetime import datetime

from prism.raven.base import Probe, ProbeHealth, RawItem


class LockedProbe(Probe):
    def __init__(self, platform: str, probe_id: str, reason: str) -> None:
        self.platform = platform
        self.probe_id = probe_id
        self.reason = reason

    async def fetch(self, query_or_feed: str, since: datetime, limit: int = 100) -> list[RawItem]:
        raise RuntimeError(self.reason)

    async def health(self) -> ProbeHealth:
        return ProbeHealth(
            probe_id=self.probe_id,
            platform=self.platform,
            last_success_at=None,
            consecutive_failures=0,
            fallback_ratio_24h=0.0,
            mean_lag_seconds=None,
            status="down",
            detail=self.reason,
        )
