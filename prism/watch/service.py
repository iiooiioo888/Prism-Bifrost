"""研判管道：校準、突發、優先級與告警。不改寫歸併關係。"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from prism.audit.log import append_audit
from prism.clock import trailing_hour
from prism.config import Settings
from prism.db.models import (
    AlertRow,
    CanonicalEvent,
    Entity,
    EventAssessment,
    EventEntity,
    EventMention,
    EventMetrics,
    EventNarrative,
    MentionLabel,
    MergeSuggestion,
)
from prism.watch.alerts import AlertContractError, build_alert, fired_rules
from prism.watch.burst import burst_score, normalize_burst
from prism.watch.calibrate import calibrate
from prism.watch.priority import PriorityParts, score_priority
from prism.weave.metrics import (
    MentionStat,
    compute_window,
    lexicon_sentiment,
    mentions_in_window,
    persist_ready,
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _band_map(raw: dict) -> dict[str, tuple[float, float]]:
    return {key: (float(value[0]), float(value[1])) for key, value in raw.items()}


def _multiplier_map(raw: dict) -> dict[str, float]:
    return {key: float(value) for key, value in raw.items()}


class WatchService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        calibration = settings.watch.get("calibration", {})
        burst = settings.watch.get("burst", {})
        priority = settings.watch.get("priority", {})
        self.bands = _band_map(calibration.get("bands", {}))
        self.multipliers = _multiplier_map(calibration.get("multipliers", {}))
        self.abstain_threshold = float(calibration.get("abstain_threshold", 0.35))
        self.fallback_penalty = float(calibration.get("fallback_penalty", 0.90))
        self.bayesian_factor = float(calibration.get("bayesian_factor", 1.0))
        self.z_threshold = float(burst.get("z_threshold", 3.0))
        self.baseline_days = int(burst.get("baseline_days", 7))
        self.eps = float(burst.get("eps", 1e-6))
        self.norm_divisor = float(burst.get("norm_divisor", 3.0))
        weights = priority.get("weights", [0.20, 0.25, 0.20, 0.20, 0.10, 0.05])
        self.weights = tuple(float(item) for item in weights)
        self.alert_threshold = float(priority.get("alert_threshold", 0.75))

    def score_event(self, event_id: str, now: datetime | None = None) -> EventAssessment:
        moment = _aware(now or datetime.now(timezone.utc))
        event = self.session.get(CanonicalEvent, event_id)
        if event is None:
            raise KeyError(event_id)
        mentions = self.session.scalars(select(EventMention).where(EventMention.event_id == event_id)).all()
        excluded = self._excluded_ids()
        stats = [self._stat(mention) for mention in mentions]
        usable = [item for item in stats if item.mention_id not in excluded and not item.abstain]
        recent_start = moment - timedelta(hours=24)
        recent = [
            item
            for item in usable
            if item.upstream_timestamp is not None and recent_start <= item.upstream_timestamp < moment
        ]
        basis = recent or usable
        calibrations = [item.calibrated_confidence for item in basis]
        average_confidence = sum(calibrations) / len(calibrations) if calibrations else 0.0
        abstain = not usable
        promoted = self._promoted(event_id)
        if promoted:
            abstain = False

        burst = self._burst(stats, moment)
        self._write_metrics(event_id, stats, moment, burst.score if burst.sample_count else None)

        domains = [item.domain for item in basis]
        diversity = (len(set(domains)) / len(domains)) if domains else 0.0
        source_types = {item.source_type for item in basis}
        cross_domain = min(1.0, len(source_types) / 2) if source_types else 0.0
        relevance = self._entity_relevance(event_id)
        unsupported = self._unsupported_ratio(event_id)
        priority = score_priority(
            PriorityParts(
                source_diversity_norm=diversity,
                calibrated_confidence_avg=average_confidence,
                burst_score_norm=normalize_burst(burst.score, self.norm_divisor),
                entity_relevance=relevance,
                cross_domain_support=cross_domain,
                unsupported_ratio=unsupported,
            ),
            weights=self.weights,  # type: ignore[arg-type]
            alert_threshold=self.alert_threshold,
        )
        assessment = self.session.get(EventAssessment, event_id)
        if assessment is None:
            assessment = EventAssessment(event_id=event_id, computed_at=moment)
            self.session.add(assessment)
        assessment.priority = round(priority.priority, 4)
        assessment.calibrated_confidence = round(average_confidence, 4)
        assessment.abstain = 1 if abstain else 0
        assessment.burst_score = round(burst.score, 3)
        assessment.promoted = 1 if promoted else 0
        assessment.computed_at = moment
        assessment.components = {
            "source_diversity_norm": diversity,
            "calibrated_confidence_avg": average_confidence,
            "burst_score": burst.score,
            "burst_score_norm": normalize_burst(burst.score, self.norm_divisor),
            "baseline_median": burst.baseline_median,
            "mad": burst.mad,
            "entity_relevance": relevance,
            "cross_domain_support": cross_domain,
            "unsupported_ratio": unsupported,
            "weights": list(self.weights),
            "usable_mentions": len(usable),
            "mention_count": len(stats),
        }
        if priority.high_priority and not abstain:
            self._maybe_alert(event, assessment, usable or stats, moment)
        return assessment

    def score_all(self, now: datetime | None = None) -> list[EventAssessment]:
        moment = now or datetime.now(timezone.utc)
        events = self.session.scalars(select(CanonicalEvent).where(CanonicalEvent.status == "active")).all()
        return [self.score_event(event.event_id, moment) for event in events]

    def _stat(self, mention: EventMention) -> MentionStat:
        result = calibrate(
            mention.source_type,
            mention.acquisition_mode,
            self.bands,
            self.multipliers,
            abstain_threshold=self.abstain_threshold,
            fallback_penalty=self.fallback_penalty,
            bayesian_factor=self.bayesian_factor,
        )
        return MentionStat(
            mention_id=mention.mention_id,
            event_id=mention.event_id or "",
            upstream_timestamp=_aware(mention.upstream_timestamp) if mention.upstream_timestamp else None,
            collected_at=_aware(mention.collected_at),
            platform=mention.platform,
            domain=(urlsplit(mention.source_url).hostname or "").lower(),
            source_type=mention.source_type,
            acquisition_mode=mention.acquisition_mode,
            calibrated_confidence=result.calibrated_confidence,
            abstain=result.abstain,
            duplicate_of=mention.duplicate_of,
            sentiment=lexicon_sentiment(mention.body),
        )

    def _burst(self, stats: list[MentionStat], now: datetime):
        current_start, current_end = trailing_hour(now, 0)
        current = len(mentions_in_window(stats, current_start, current_end))
        baseline: list[float] = []
        for day in range(1, self.baseline_days + 1):
            start, end = trailing_hour(now, day)
            baseline.append(float(len(mentions_in_window(stats, start, end))))
        return burst_score(current, baseline, z_threshold=self.z_threshold, eps=self.eps)

    def _write_metrics(self, event_id: str, stats: list[MentionStat], now: datetime, burst_value: float | None) -> None:
        windows = [
            ("1h", now - timedelta(hours=1), now, burst_value),
            ("6h", now - timedelta(hours=6), now, None),
            ("24h", now - timedelta(hours=24), now, None),
            ("7d", now - timedelta(days=7), now, None),
        ]
        for _name, start, end, burst_for_window in windows:
            previous = {item.platform for item in mentions_in_window(stats, start - (end - start), start)}
            computed = compute_window(stats, start, end, previous, burst_for_window)
            persist_ready(
                self.session,
                EventMetrics(
                    event_id=event_id,
                    window_start=start,
                    window_end=end,
                    mention_count=computed.mention_count,
                    source_diversity=round(computed.source_diversity, 4),
                    platform_entropy=round(computed.platform_entropy, 4),
                    diffusion_rate=round(computed.diffusion_rate, 4),
                    sentiment_dist=computed.sentiment_dist,
                    burst_score=None if computed.burst_score is None else round(computed.burst_score, 3),
                    computed_at=now,
                ),
            )

    def _entity_relevance(self, event_id: str) -> float:
        rows = self.session.execute(
            select(EventEntity.confidence, Entity.watched)
            .join(Entity, Entity.entity_id == EventEntity.entity_id)
            .where(EventEntity.event_id == event_id)
        ).all()
        watched = [float(confidence) for confidence, flag in rows if flag]
        if not watched:
            return 0.0
        return max(watched)

    def _unsupported_ratio(self, event_id: str) -> float:
        narratives = self.session.scalars(select(EventNarrative).where(EventNarrative.event_id == event_id)).all()
        suggestions = self.session.scalars(
            select(MergeSuggestion).where(
                (MergeSuggestion.left_event_id == event_id) | (MergeSuggestion.right_event_id == event_id),
                MergeSuggestion.source == "model",
            )
        ).all()
        total = len(narratives) + len(suggestions)
        if total == 0:
            return 0.0
        bad = sum(1 for item in narratives if item.unsupported) + sum(
            1 for item in suggestions if (item.payload or {}).get("unsupported")
        )
        return bad / total

    def _excluded_ids(self) -> set[str]:
        labels = self.session.scalars(select(MentionLabel).order_by(MentionLabel.created_at.asc())).all()
        latest: dict[str, str] = {}
        for label in labels:
            latest[label.mention_id] = label.label
        return {mention_id for mention_id, label in latest.items() if label == "exclude"}

    def _promoted(self, event_id: str) -> bool:
        current = self.session.get(EventAssessment, event_id)
        return bool(current and current.promoted)

    def _maybe_alert(self, event: CanonicalEvent, assessment: EventAssessment, stats: list[MentionStat], now: datetime) -> None:
        urls = []
        for mention in self.session.scalars(select(EventMention).where(EventMention.event_id == event.event_id)).all():
            stat = next((item for item in stats if item.mention_id == mention.mention_id), None)
            if stat is None or stat.abstain:
                continue
            if mention.mention_id in self._excluded_ids():
                continue
            urls.append(mention.source_url)
        components = assessment.components or {}
        rules = fired_rules(
            priority=float(assessment.priority),
            alert_threshold=self.alert_threshold,
            burst_score=float(assessment.burst_score),
            burst_threshold=self.z_threshold,
            entity_relevance=float(components.get("entity_relevance") or 0),
            source_diversity=float(components.get("source_diversity_norm") or 0),
            calibrated_confidence=float(assessment.calibrated_confidence),
            cross_domain_support=float(components.get("cross_domain_support") or 0),
        )
        try:
            draft = build_alert(
                alert_id=str(uuid.uuid4()),
                event_id=event.event_id,
                priority=float(assessment.priority),
                trigger_rules=rules,
                calibrated_confidence=float(assessment.calibrated_confidence),
                abstain=False,
                signal_urls=urls,
                summary=f"待人工確認：{event.title}",
                created_at=now,
            )
        except AlertContractError:
            return
        recent = self.session.scalars(
            select(AlertRow).where(AlertRow.event_id == event.event_id, AlertRow.created_at >= now - timedelta(hours=1))
        ).all()
        signature = sorted(draft.trigger_rules)
        if any(sorted(item.trigger_rules or []) == signature for item in recent):
            return
        self.session.add(
            AlertRow(
                alert_id=draft.alert_id,
                event_id=draft.event_id,
                priority=draft.priority,
                trigger_rules=list(draft.trigger_rules),
                calibrated_confidence=draft.calibrated_confidence,
                abstain=0,
                signal_urls=list(draft.signal_urls),
                summary=draft.summary,
                created_at=now,
            )
        )
        append_audit(
            self.session,
            actor="system:watch",
            action="watch.alert",
            target_type="alert",
            target_id=draft.alert_id,
            payload=draft.as_dict(),
            created_at=now,
        )


def platform_histogram(stats: list[MentionStat]) -> dict[str, int]:
    return dict(Counter(item.platform for item in stats))
