"""原始條目入庫。去重發生在寫入提及之前。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from prism.audit.log import append_audit
from prism.bridge.normalize import BloomFilter, content_hash, normalize_url, simhash, simhash_similarity
from prism.bridge.objects import ObjectStore
from prism.db.models import EventMention, IngestLog, MentionFingerprint, TimestampGapQueue, UrlSeen
from prism.raven.base import RawItem
from prism.raven.compliance import ComplianceError, SitePolicy


@dataclass
class IngestReport:
    stored: list[EventMention] = field(default_factory=list)
    dropped_url: int = 0
    dropped_hash: int = 0
    near_duplicates: int = 0
    rejected: list[str] = field(default_factory=list)


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class IngestService:
    def __init__(
        self,
        session: Session,
        object_store: ObjectStore,
        policy: SitePolicy,
        bloom_size: int = 10_000_000,
        near_dup_threshold: float = 0.95,
    ) -> None:
        self.session = session
        self.object_store = object_store
        self.policy = policy
        self.near_dup_threshold = near_dup_threshold
        self.bloom = BloomFilter(bloom_size)
        for seen in session.scalars(select(UrlSeen.normalized_url)):
            self.bloom.add(seen)

    def accept(self, items: list[RawItem], actor: str = "system:ingest") -> IngestReport:
        report = IngestReport()
        for item in items:
            try:
                self.policy.assert_allowed(item.source_url)
            except ComplianceError as exc:
                report.rejected.append(str(exc))
                self._log(item.probe_id, item.source_url, "rejected", str(exc))
                continue
            stored = self._accept_one(item, actor, report)
            if stored is not None:
                report.stored.append(stored)
        return report

    def _accept_one(self, item: RawItem, actor: str, report: IngestReport) -> EventMention | None:
        now = item.collected_at if item.collected_at.tzinfo else item.collected_at.replace(tzinfo=timezone.utc)
        normalized = normalize_url(item.source_url)
        digest = item.content_hash or content_hash(item.body)
        if self.session.scalar(select(EventMention.mention_id).where(EventMention.content_hash == digest)):
            report.dropped_hash += 1
            self._log(item.probe_id, item.source_url, "duplicate_hash", digest)
            return None
        if normalized in self.bloom:
            existing = self.session.scalar(select(UrlSeen).where(UrlSeen.normalized_url == normalized))
            if existing is not None:
                report.dropped_url += 1
                self._log(item.probe_id, item.source_url, "duplicate_url", normalized)
                return None
        fingerprint = simhash(item.body)
        duplicate_of = self._near_duplicate(fingerprint)
        raw_ref = item.raw_ref
        if not raw_ref:
            payload = "\n".join(
                [
                    item.source_url,
                    item.title or "",
                    item.author or "",
                    item.body,
                ]
            ).encode("utf-8")
            raw_ref = self.object_store.put(payload)
        mention = EventMention(
            event_id=None,
            source_url=item.source_url,
            upstream_timestamp=item.upstream_timestamp,
            collected_at=now,
            platform=item.platform,
            author=item.author,
            title=item.title,
            body=item.body,
            language=item.language or "zh",
            acquisition_mode=item.acquisition_mode,
            raw_ref=raw_ref,
            content_hash=digest,
            duplicate_of=duplicate_of,
            source_type=item.source_type,
            created_at=now,
        )
        self.session.add(mention)
        self.session.flush()
        self.session.add(UrlSeen(normalized_url=normalized, mention_id=mention.mention_id, first_seen=now))
        self.session.add(MentionFingerprint(mention_id=mention.mention_id, simhash=str(fingerprint)))
        self.bloom.add(normalized)
        if item.upstream_timestamp is None:
            self.session.add(TimestampGapQueue(mention_id=mention.mention_id, queued_at=now))
        if duplicate_of:
            report.near_duplicates += 1
            self._log(item.probe_id, item.source_url, "near_duplicate", duplicate_of)
        else:
            self._log(item.probe_id, item.source_url, "stored", mention.mention_id)
        append_audit(
            self.session,
            actor=actor,
            action="ingest.store",
            target_type="event_mention",
            target_id=mention.mention_id,
            payload={
                "source_url": item.source_url,
                "normalized_url": normalized,
                "duplicate_of": duplicate_of,
                "acquisition_mode": item.acquisition_mode,
                "timestamp_missing": item.upstream_timestamp is None,
            },
            created_at=now,
        )
        return mention

    def _near_duplicate(self, fingerprint: int) -> str | None:
        rows = self.session.execute(select(MentionFingerprint.mention_id, MentionFingerprint.simhash)).all()
        best_id = None
        best_score = 0.0
        for mention_id, stored in rows:
            score = simhash_similarity(fingerprint, int(stored))
            if score >= self.near_dup_threshold and score > best_score:
                best_id = mention_id
                best_score = score
        return best_id

    def _log(self, probe_id: str, source_url: str | None, outcome: str, detail: str) -> None:
        self.session.add(
            IngestLog(
                probe_id=probe_id,
                source_url=source_url,
                outcome=outcome,
                detail=detail,
                created_at=datetime.now(timezone.utc),
            )
        )
