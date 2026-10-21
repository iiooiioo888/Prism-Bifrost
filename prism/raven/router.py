"""探針路由與降級。首選失敗才走備選，並明示採集模式。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from prism.raven.base import Probe, RawItem


class ProbeUnavailable(RuntimeError):
    """全部後端失敗。"""


@dataclass
class RouteAttempt:
    backend_id: str
    success: bool
    error: str | None = None


@dataclass
class RouteResult:
    items: list[RawItem]
    acquisition_mode: str
    backend_id: str
    attempts: list[RouteAttempt] = field(default_factory=list)


@dataclass(frozen=True)
class Backend:
    probe: Probe
    priority: int
    backend_id: str


class ProbeRouter:
    def __init__(self, backends: list[Backend]) -> None:
        if not backends:
            raise ValueError("探針至少需要一個後端")
        self.backends = sorted(backends, key=lambda item: item.priority)

    async def fetch(self, query_or_feed: str, since: datetime, limit: int = 100) -> RouteResult:
        attempts: list[RouteAttempt] = []
        for index, backend in enumerate(self.backends):
            try:
                items = await backend.probe.fetch(query_or_feed, since, limit)
            except Exception as exc:  # noqa: BLE001 — 路由層必須吞掉單後端失敗才能降級
                attempts.append(RouteAttempt(backend.backend_id, False, str(exc)))
                continue
            mode = "live" if index == 0 else "fallback"
            stamped: list[RawItem] = []
            for item in items:
                stamped.append(
                    RawItem(
                        source_url=item.source_url,
                        upstream_timestamp=item.upstream_timestamp,
                        collected_at=item.collected_at,
                        platform=item.platform,
                        title=item.title,
                        body=item.body,
                        author=item.author,
                        language=item.language,
                        acquisition_mode=mode,  # type: ignore[arg-type]
                        raw_ref=item.raw_ref,
                        probe_id=item.probe_id or backend.probe.probe_id,
                        content_hash=item.content_hash,
                        source_type=item.source_type,
                    )
                )
            attempts.append(RouteAttempt(backend.backend_id, True))
            return RouteResult(stamped, mode, backend.backend_id, attempts)
        raise ProbeUnavailable("全部後端失敗：" + "; ".join(item.error or "" for item in attempts))
