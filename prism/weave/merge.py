"""歸併引擎。只有高置信雙命中或人工確認會寫事實層關聯。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from prism.audit.log import append_audit
from prism.bridge.normalize import normalize_url
from prism.config import Settings
from prism.db.models import (
    CanonicalEvent,
    Entity,
    EntityAlias,
    EntityReviewQueue,
    EventEntity,
    EventMention,
    MergeSuggestion,
)
from prism.weave.entities import EntityRecord, entity_similarity, match_entities
from prism.weave.scoring import MergeScore, score_merge
from prism.weave.similarity import MentionView, entity_overlap, rule_features, semantic_features


@dataclass
class Assignment:
    mention_id: str
    event_id: str
    band: str
    linked_existing: bool
    suggestion_id: str | None = None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class MergeEngine:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        merge_config = settings.weave.get("merge", {})
        entity_config = settings.weave.get("entity", {})
        self.auto_threshold = float(merge_config.get("auto_threshold", 0.85))
        self.review_threshold = float(merge_config.get("review_threshold", 0.65))
        self.semantic_hit_threshold = float(merge_config.get("semantic_hit_threshold", 0.60))
        self.title_rule_threshold = float(merge_config.get("title_rule_threshold", 0.80))
        self.author_window_hours = float(merge_config.get("author_window_hours", 6))
        self.entity_review_threshold = float(entity_config.get("review_threshold", 0.72))

    def assign(self, mention: EventMention, actor: str = "system:rule") -> Assignment:
        catalog = self._catalog()
        if mention.duplicate_of:
            original = self.session.get(EventMention, mention.duplicate_of)
            if original is None or original.event_id is None:
                raise RuntimeError("近似重複找不到原始提及")
            mention.event_id = original.event_id
            mention.link_method = original.link_method
            mention.link_confidence = original.link_confidence
            self._attach_entities(mention, catalog, actor)
            append_audit(
                self.session,
                actor=actor,
                action="weave.near_duplicate_attach",
                target_type="event_mention",
                target_id=mention.mention_id,
                payload={"event_id": mention.event_id, "duplicate_of": mention.duplicate_of},
            )
            return Assignment(mention.mention_id, mention.event_id, "high", True)

        best_event, best_mention, best_score = self._best_candidate(mention, catalog)
        now = _aware(mention.collected_at)
        if best_score is not None and best_score.band == "high" and best_event is not None:
            mention.event_id = best_event.event_id
            mention.link_method = "rule" if best_score.rule_score >= best_score.semantic_score else "semantic"
            mention.link_confidence = round(best_score.score, 3)
            best_event.last_updated = now
            links = list((best_event.provenance or {}).get("links", []))
            links.append(
                {
                    "mention_id": mention.mention_id,
                    "actor": actor,
                    "reason": "高置信且規則與語義同時命中",
                    "score": best_score.score,
                    "at": now.isoformat(),
                }
            )
            best_event.provenance = {**(best_event.provenance or {}), "links": links}
            self._attach_entities(mention, catalog, actor)
            append_audit(
                self.session,
                actor=actor,
                action="weave.auto_merge",
                target_type="event_mention",
                target_id=mention.mention_id,
                payload={
                    "event_id": best_event.event_id,
                    "score": best_score.score,
                    "rule_hit": best_score.rule_hit,
                    "semantic_hit": best_score.semantic_hit,
                    "link_method": mention.link_method,
                },
            )
            return Assignment(mention.mention_id, best_event.event_id, "high", True)

        event = CanonicalEvent(
            event_type="observation",
            title=mention.title or mention.source_url,
            first_seen=mention.upstream_timestamp or now,
            last_updated=now,
            status="active",
            merged_from=[],
            provenance={"created_by": "system:ingest"},
            created_at=now,
        )
        self.session.add(event)
        self.session.flush()
        mention.event_id = event.event_id
        self._attach_entities(mention, catalog, actor)
        suggestion_id = None
        if best_score is not None and best_score.band == "medium" and best_event is not None and best_mention is not None:
            suggestion = MergeSuggestion(
                left_event_id=best_event.event_id,
                right_event_id=event.event_id,
                left_mention_id=best_mention.mention_id,
                right_mention_id=mention.mention_id,
                score=round(best_score.score, 4),
                band="medium",
                source="semantic" if best_score.semantic_score > best_score.rule_score else "rule",
                payload={
                    "rule_score": best_score.rule_score,
                    "semantic_score": best_score.semantic_score,
                    "entity_overlap_score": best_score.entity_overlap_score,
                    "rule_hit": best_score.rule_hit,
                    "semantic_hit": best_score.semantic_hit,
                    "model_decides_merge": False,
                },
                status="pending",
                created_at=now,
            )
            self.session.add(suggestion)
            self.session.flush()
            suggestion_id = suggestion.suggestion_id
            append_audit(
                self.session,
                actor="system:weave",
                action="weave.suggest_merge",
                target_type="merge_suggestion",
                target_id=suggestion.suggestion_id,
                payload={
                    "left_event_id": best_event.event_id,
                    "right_event_id": event.event_id,
                    "score": best_score.score,
                    "fact_layer_changed": False,
                },
            )
        return Assignment(mention.mention_id, event.event_id, best_score.band if best_score else "low", False, suggestion_id)

    def _best_candidate(
        self,
        mention: EventMention,
        catalog: list[EntityRecord],
    ) -> tuple[CanonicalEvent | None, EventMention | None, MergeScore | None]:
        incoming = self._view(mention, catalog)
        mentions = self.session.scalars(
            select(EventMention).where(
                EventMention.duplicate_of.is_(None),
                EventMention.mention_id != mention.mention_id,
                EventMention.event_id.is_not(None),
            )
        ).all()
        best: tuple[CanonicalEvent | None, EventMention | None, MergeScore | None] = (None, None, None)
        for other in mentions:
            event = self.session.get(CanonicalEvent, other.event_id)
            if event is None or event.status != "active":
                continue
            scored = self._score_pair(incoming, self._view(other, catalog))
            if best[2] is None or scored.score > best[2].score:
                best = (event, other, scored)
        return best

    def _score_pair(self, left: MentionView, right: MentionView) -> MergeScore:
        rule_score, rule_hit, _title = rule_features(
            left,
            right,
            title_threshold=self.title_rule_threshold,
            author_window_hours=self.author_window_hours,
        )
        semantic_score, semantic_hit = semantic_features(left, right, self.semantic_hit_threshold)
        overlap = entity_overlap(left.entity_ids, right.entity_ids)
        return score_merge(
            rule_score,
            semantic_score,
            overlap,
            rule_hit,
            semantic_hit,
            auto_threshold=self.auto_threshold,
            review_threshold=self.review_threshold,
        )

    def _view(self, mention: EventMention, catalog: list[EntityRecord]) -> MentionView:
        text = f"{mention.title or ''}\n{mention.body}"
        hits = match_entities(text, catalog)
        return MentionView(
            title=mention.title or "",
            body=mention.body,
            author=mention.author,
            normalized_url=normalize_url(mention.source_url),
            upstream_timestamp=mention.upstream_timestamp,
            entity_ids=frozenset(entity.entity_id for entity, _name in hits),
        )

    def _catalog(self) -> list[EntityRecord]:
        entities = self.session.scalars(select(Entity)).all()
        records: list[EntityRecord] = []
        for entity in entities:
            aliases = self.session.scalars(select(EntityAlias.alias).where(EntityAlias.entity_id == entity.entity_id)).all()
            records.append(
                EntityRecord(
                    entity_id=entity.entity_id,
                    name=entity.name,
                    aliases=tuple(aliases),
                    description=entity.description,
                )
            )
        return records

    def _has_entity_link(self, event_id: str, entity_id: str) -> bool:
        if self.session.get(EventEntity, (event_id, entity_id)) is not None:
            return True
        return any(
            isinstance(obj, EventEntity) and obj.event_id == event_id and obj.entity_id == entity_id
            for obj in self.session.new
        )

    def _attach_entities(self, mention: EventMention, catalog: list[EntityRecord], actor: str) -> None:
        if mention.event_id is None:
            return
        text = f"{mention.title or ''}\n{mention.body}"
        exact = {entity.entity_id for entity, _name in match_entities(text, catalog)}
        now = datetime.now(timezone.utc)
        for entity in catalog:
            if entity.entity_id in exact:
                if not self._has_entity_link(mention.event_id, entity.entity_id):
                    self.session.add(
                        EventEntity(
                            event_id=mention.event_id,
                            entity_id=entity.entity_id,
                            role="mentioned",
                            confidence=1,
                            link_method="rule",
                        )
                    )
                continue
            similarity = entity_similarity(text, entity.description)
            if similarity >= self.entity_review_threshold:
                self.session.add(
                    EntityReviewQueue(
                        event_id=mention.event_id,
                        entity_id=entity.entity_id,
                        score=round(similarity, 4),
                        status="pending",
                        created_at=now,
                    )
                )
                append_audit(
                    self.session,
                    actor=actor,
                    action="weave.entity_review",
                    target_type="entity_review_queue",
                    target_id=f"{mention.event_id}:{entity.entity_id}",
                    payload={"score": similarity, "fact_layer_changed": False},
                )
