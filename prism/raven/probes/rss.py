"""訂閱來源探針。只讀取已允許的公開 feed。"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser

from prism.bridge.normalize import content_hash
from prism.clock import ensure_utc
from prism.raven.base import Probe, ProbeHealth, RawItem
from prism.raven.compliance import SitePolicy


def _published(entry: dict) -> datetime | None:
    if entry.get("published_parsed"):
        stamp = entry.published_parsed
        return datetime(*stamp[:6], tzinfo=timezone.utc)
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        return ensure_utc(parsedate_to_datetime(raw))
    except (TypeError, ValueError, IndexError):
        return None


class RssProbe(Probe):
    platform = "rss"

    def __init__(self, policy: SitePolicy, probe_id: str = "rss", source_type: str = "web") -> None:
        self.policy = policy
        self.probe_id = probe_id
        self.source_type = source_type
        self.last_error: str | None = None
        self.last_success_at: datetime | None = None

    async def fetch(self, query_or_feed: str, since: datetime, limit: int = 100) -> list[RawItem]:
        target = query_or_feed
        if target.startswith("http://") or target.startswith("https://"):
            self.policy.assert_allowed(target)
            parsed = feedparser.parse(target)
        else:
            path = Path(target)
            if not path.exists():
                raise FileNotFoundError(target)
            parsed = feedparser.parse(path.read_bytes())
        if getattr(parsed, "bozo", False) and not parsed.entries:
            self.last_error = str(getattr(parsed, "bozo_exception", "訂閱來源無法解析"))
            raise RuntimeError(self.last_error)
        items: list[RawItem] = []
        collected = datetime.now(timezone.utc)
        for entry in parsed.entries[:limit]:
            link = entry.get("link") or ""
            if not link:
                continue
            self.policy.assert_allowed(link)
            published = _published(entry)
            if published is not None and published < ensure_utc(since):
                continue
            body = entry.get("summary") or entry.get("description") or entry.get("title") or ""
            title = entry.get("title")
            author = entry.get("author")
            items.append(
                RawItem(
                    source_url=link,
                    upstream_timestamp=published,
                    collected_at=collected,
                    platform=self.platform,
                    title=title,
                    body=body,
                    author=author,
                    language="zh",
                    acquisition_mode="live",
                    raw_ref="",
                    probe_id=self.probe_id,
                    content_hash=content_hash(body),
                    source_type=self.source_type,
                )
            )
        self.last_success_at = collected
        self.last_error = None
        return items

    async def health(self) -> ProbeHealth:
        status = "healthy" if self.last_success_at and not self.last_error else "down"
        return ProbeHealth(
            probe_id=self.probe_id,
            platform=self.platform,
            last_success_at=self.last_success_at,
            consecutive_failures=0 if status == "healthy" else 1,
            fallback_ratio_24h=0.0,
            mean_lag_seconds=None,
            status=status,  # type: ignore[arg-type]
            detail=self.last_error or "訂閱來源可讀",
        )
