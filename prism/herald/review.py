"""人工複核。只有這裡的顯式動作可以改寫事實層關聯。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from prism.audit.log import append_audit
from prism.bridge.normalize import normalize_url
from prism.config import Settings
from prism.db.models import (
    AnalysisNote,
    CanonicalEvent,
    EventAssessment,
    EventMention,
    MentionFingerprint,
    MentionLabel,
    MergeSuggestion,
    ReviewAction,
    TimestampGapQueue,
    UrlSeen,
)


class ReviewError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event_snapshot(session: Session, event_id: str) -> dict[str, Any]:
    event = session.get(CanonicalEvent, event_id)
    if event is None:
        return {}
    mentions = session.scalars(select(EventMention.mention_id).where(EventMention.event_id == event_id)).all()
    return {
        "event_id": event.event_id,
        "status": event.status,
        "title": event.title,
        "merged_from": list(event.merged_from or []),
        "mention_ids": list(mentions),
    }


def _review(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: str,
    actor: str,
    reason: str | None,
    before_state: dict,
    after_state: dict,
) -> ReviewAction:
    row = ReviewAction(
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor=actor,
        reason=reason,
        before_state=before_state,
        after_state=after_state,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    append_audit(
        session,
        actor=actor,
        action=f"review.{action}",
        target_type=target_type,
        target_id=target_id,
        payload={"reason": reason, "before": before_state, "after": after_state, "review_action_id": row.action_id},
    )
    return row


def merge_events(session: Session, source_event_id: str, target_event_id: str, actor: str, reason: str | None) -> CanonicalEvent:
    if source_event_id == target_event_id:
        raise ReviewError("不能把事件歸併到自己")
    source = session.get(CanonicalEvent, source_event_id)
    target = session.get(CanonicalEvent, target_event_id)
    if source is None or target is None:
        raise ReviewError("事件不存在")
    if source.status == "merged":
        raise ReviewError("來源事件已經歸併")
    before = {"source": _event_snapshot(session, source_event_id), "target": _event_snapshot(session, target_event_id)}
    mentions = session.scalars(select(EventMention).where(EventMention.event_id == source_event_id)).all()
    for mention in mentions:
        mention.event_id = target_event_id
        mention.link_method = "human"
        mention.link_confidence = 1
    source.status = "merged"
    target.merged_from = [*(target.merged_from or []), source_event_id]
    target.last_updated = _now()
    for suggestion in session.scalars(
        select(MergeSuggestion).where(
            or_(
                (MergeSuggestion.left_event_id == source_event_id) & (MergeSuggestion.right_event_id == target_event_id),
                (MergeSuggestion.left_event_id == target_event_id) & (MergeSuggestion.right_event_id == source_event_id),
            ),
            MergeSuggestion.status == "pending",
        )
    ).all():
        suggestion.status = "accepted"
    after = {"source": _event_snapshot(session, source_event_id), "target": _event_snapshot(session, target_event_id)}
    _review(
        session,
        action="merge",
        target_type="canonical_event",
        target_id=target_event_id,
        actor=actor,
        reason=reason,
        before_state=before,
        after_state=after,
    )
    return target


def split_event(
    session: Session,
    event_id: str,
    mention_ids: list[str],
    actor: str,
    reason: str | None,
    title: str | None,
) -> CanonicalEvent:
    event = session.get(CanonicalEvent, event_id)
    if event is None:
        raise ReviewError("事件不存在")
    if not mention_ids:
        raise ReviewError("拆分至少需要一條提及")
    before = _event_snapshot(session, event_id)
    mentions = session.scalars(select(EventMention).where(EventMention.mention_id.in_(mention_ids))).all()
    if len(mentions) != len(set(mention_ids)):
        raise ReviewError("有提及不存在")
    if any(mention.event_id != event_id for mention in mentions):
        raise ReviewError("提及不屬於此事件")
    now = _now()
    created = CanonicalEvent(
        event_type=event.event_type,
        title=title or f"{event.title}（拆分）",
        first_seen=min((mention.upstream_timestamp or mention.collected_at) for mention in mentions),
        last_updated=now,
        status="active",
        merged_from=[],
        provenance={"split_from": event_id, "actor": actor},
        created_at=now,
    )
    session.add(created)
    session.flush()
    for mention in mentions:
        mention.event_id = created.event_id
        mention.link_method = "human"
        mention.link_confidence = 1
    remaining = session.scalars(select(EventMention.mention_id).where(EventMention.event_id == event_id)).all()
    if not remaining:
        event.status = "split"
    event.last_updated = now
    after = {"original": _event_snapshot(session, event_id), "created": _event_snapshot(session, created.event_id)}
    _review(
        session,
        action="split",
        target_type="canonical_event",
        target_id=event_id,
        actor=actor,
        reason=reason,
        before_state=before,
        after_state=after,
    )
    return created


def label_mention(session: Session, mention_id: str, label: str, actor: str, note: str | None) -> MentionLabel:
    if label not in {"confirm", "exclude", "uncertain"}:
        raise ReviewError("標註必須是確認、排除或存疑")
    mention = session.get(EventMention, mention_id)
    if mention is None:
        raise ReviewError("提及不存在")
    action = {"confirm": "confirm", "exclude": "exclude", "uncertain": "annotate"}[label]
    before = {"mention_id": mention_id, "event_id": mention.event_id}
    row = MentionLabel(
        mention_id=mention_id,
        label=label,
        note=note,
        actor=actor,
        created_at=_now(),
    )
    session.add(row)
    if note:
        session.add(
            AnalysisNote(
                target_type="event_mention",
                target_id=mention_id,
                body=note,
                actor=actor,
                created_at=_now(),
            )
        )
    session.flush()
    _review(
        session,
        action=action,
        target_type="event_mention",
        target_id=mention_id,
        actor=actor,
        reason=note,
        before_state=before,
        after_state={"label": label, "event_id": mention.event_id, "fact_link_unchanged": True},
    )
    return row


def promote_event(session: Session, event_id: str, actor: str, reason: str | None) -> EventAssessment:
    event = session.get(CanonicalEvent, event_id)
    if event is None:
        raise ReviewError("事件不存在")
    assessment = session.get(EventAssessment, event_id)
    before = {"promoted": bool(assessment.promoted) if assessment else False, "abstain": bool(assessment.abstain) if assessment else None}
    if assessment is None:
        assessment = EventAssessment(
            event_id=event_id,
            priority=0,
            calibrated_confidence=0,
            abstain=1,
            burst_score=0,
            components={},
            promoted=1,
            computed_at=_now(),
        )
        session.add(assessment)
    else:
        assessment.promoted = 1
    session.flush()
    _review(
        session,
        action="confirm",
        target_type="canonical_event",
        target_id=event_id,
        actor=actor,
        reason=reason,
        before_state=before,
        after_state={"promoted": True},
    )
    return assessment


def accept_suggestion(session: Session, suggestion_id: str, actor: str, reason: str | None) -> CanonicalEvent:
    suggestion = session.get(MergeSuggestion, suggestion_id)
    if suggestion is None:
        raise ReviewError("歸併建議不存在")
    if suggestion.status != "pending":
        raise ReviewError("歸併建議已處理")
    return merge_events(session, suggestion.right_event_id, suggestion.left_event_id, actor, reason or "採納歸併建議")


def reject_suggestion(session: Session, suggestion_id: str, actor: str, reason: str | None) -> MergeSuggestion:
    suggestion = session.get(MergeSuggestion, suggestion_id)
    if suggestion is None:
        raise ReviewError("歸併建議不存在")
    before = {"status": suggestion.status}
    suggestion.status = "rejected"
    _review(
        session,
        action="exclude",
        target_type="merge_suggestion",
        target_id=suggestion_id,
        actor=actor,
        reason=reason,
        before_state=before,
        after_state={"status": "rejected", "fact_layer_changed": False},
    )
    return suggestion


def erase_mentions(session: Session, *, actor: str, source_url: str | None, author: str | None, reason: str | None) -> list[str]:
    if not source_url and not author:
        raise ReviewError("刪除請求必須提供原始網址或作者")
    stmt = select(EventMention)
    if source_url and author:
        stmt = stmt.where(or_(EventMention.source_url == source_url, EventMention.author == author))
    elif source_url:
        normalized = normalize_url(source_url)
        rows = session.scalars(select(EventMention)).all()
        matched = [row.mention_id for row in rows if row.source_url == source_url or normalize_url(row.source_url) == normalized]
        return _delete_mentions(session, matched, actor, reason)
    else:
        stmt = stmt.where(EventMention.author == author)
    matched = [row.mention_id for row in session.scalars(stmt).all()]
    return _delete_mentions(session, matched, actor, reason)


def _delete_mentions(session: Session, mention_ids: list[str], actor: str, reason: str | None) -> list[str]:
    if not mention_ids:
        return []
    append_audit(
        session,
        actor=actor,
        action="compliance.erasure",
        target_type="event_mention",
        target_id=",".join(mention_ids),
        payload={"mention_ids": mention_ids, "reason": reason},
    )
    session.execute(
        EventMention.__table__.update().where(EventMention.duplicate_of.in_(mention_ids)).values(duplicate_of=None)
    )
    session.execute(delete(MergeSuggestion).where(or_(
        MergeSuggestion.left_mention_id.in_(mention_ids),
        MergeSuggestion.right_mention_id.in_(mention_ids),
    )))
    session.execute(delete(MentionLabel).where(MentionLabel.mention_id.in_(mention_ids)))
    session.execute(delete(MentionFingerprint).where(MentionFingerprint.mention_id.in_(mention_ids)))
    session.execute(delete(TimestampGapQueue).where(TimestampGapQueue.mention_id.in_(mention_ids)))
    session.execute(delete(UrlSeen).where(UrlSeen.mention_id.in_(mention_ids)))
    session.execute(delete(EventMention).where(EventMention.mention_id.in_(mention_ids)))
    return mention_ids


def rescore_after(session: Session, settings: Settings, event_ids: list[str]) -> None:
    from prism.watch.service import WatchService

    service = WatchService(session, settings)
    for event_id in event_ids:
        event = session.get(CanonicalEvent, event_id)
        if event is not None and event.status == "active":
            service.score_event(event_id)
