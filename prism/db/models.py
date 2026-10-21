"""事實層、研判層與審計表。研判表的寫入路徑不得改寫提及所屬事件。"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class CanonicalEvent(Base):
    __tablename__ = "canonical_event"
    __table_args__ = (
        CheckConstraint("status IN ('active','stale','merged','split')", name="ck_event_status"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    merged_from: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventMention(Base):
    __tablename__ = "event_mention"
    __table_args__ = (
        CheckConstraint("acquisition_mode IN ('live','fallback')", name="ck_acquisition_mode"),
        CheckConstraint(
            "link_method IS NULL OR link_method IN ('rule','semantic','human')",
            name="ck_link_method",
        ),
    )

    mention_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_event.event_id"), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    upstream_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    acquisition_mode: Mapped[str] = mapped_column(Text, nullable=False)
    raw_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("event_mention.mention_id"))
    link_method: Mapped[str | None] = mapped_column(Text)
    link_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventMetrics(Base):
    __tablename__ = "event_metrics"

    event_id: Mapped[str] = mapped_column(ForeignKey("canonical_event.event_id"), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_diversity: Mapped[float | None] = mapped_column(Numeric(5, 4))
    platform_entropy: Mapped[float | None] = mapped_column(Numeric(6, 4))
    diffusion_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))
    sentiment_dist: Mapped[dict | None] = mapped_column(JSON)
    burst_score: Mapped[float | None] = mapped_column(Numeric(8, 3))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UrlSeen(Base):
    __tablename__ = "url_seen"

    normalized_url: Mapped[str] = mapped_column(Text, primary_key=True)
    mention_id: Mapped[str] = mapped_column(ForeignKey("event_mention.mention_id"), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MentionFingerprint(Base):
    __tablename__ = "mention_fingerprint"

    mention_id: Mapped[str] = mapped_column(ForeignKey("event_mention.mention_id"), primary_key=True)
    simhash: Mapped[str] = mapped_column(String(32), nullable=False)


class TimestampGapQueue(Base):
    __tablename__ = "timestamp_gap_queue"

    mention_id: Mapped[str] = mapped_column(ForeignKey("event_mention.mention_id"), primary_key=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MergeSuggestion(Base):
    __tablename__ = "merge_suggestion"
    __table_args__ = (
        CheckConstraint("band IN ('high','medium','low')", name="ck_suggestion_band"),
        CheckConstraint("status IN ('pending','accepted','rejected')", name="ck_suggestion_status"),
    )

    suggestion_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    left_event_id: Mapped[str] = mapped_column(ForeignKey("canonical_event.event_id"), nullable=False)
    right_event_id: Mapped[str] = mapped_column(ForeignKey("canonical_event.event_id"), nullable=False)
    left_mention_id: Mapped[str] = mapped_column(ForeignKey("event_mention.mention_id"), nullable=False)
    right_mention_id: Mapped[str] = mapped_column(ForeignKey("event_mention.mention_id"), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    band: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventNarrative(Base):
    __tablename__ = "event_narrative"

    narrative_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("canonical_event.event_id"), nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    mention_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    unsupported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisNote(Base):
    __tablename__ = "analysis_note"

    note_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewAction(Base):
    __tablename__ = "review_action"
    __table_args__ = (
        CheckConstraint(
            "action IN ('confirm','exclude','merge','split','annotate')",
            name="ck_review_action",
        ),
    )

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    before_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    after_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MentionLabel(Base):
    __tablename__ = "mention_label"
    __table_args__ = (
        CheckConstraint("label IN ('confirm','exclude','uncertain')", name="ck_mention_label"),
    )

    label_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mention_id: Mapped[str] = mapped_column(ForeignKey("event_mention.mention_id"), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventAssessment(Base):
    __tablename__ = "event_assessment"

    event_id: Mapped[str] = mapped_column(ForeignKey("canonical_event.event_id"), primary_key=True)
    priority: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False, default=0)
    calibrated_confidence: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False, default=0)
    abstain: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    burst_score: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, default=0)
    components: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    promoted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Entity(Base):
    __tablename__ = "entity"

    entity_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    watched: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntityAlias(Base):
    __tablename__ = "entity_alias"
    __table_args__ = (UniqueConstraint("entity_id", "alias", name="uq_entity_alias"),)

    alias_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entity.entity_id"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)


class EventEntity(Base):
    __tablename__ = "event_entity"
    __table_args__ = (
        CheckConstraint("link_method IN ('rule','semantic','human')", name="ck_entity_link_method"),
    )

    event_id: Mapped[str] = mapped_column(ForeignKey("canonical_event.event_id"), primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entity.entity_id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="mentioned")
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    link_method: Mapped[str] = mapped_column(Text, nullable=False)


class EntityReviewQueue(Base):
    __tablename__ = "entity_review_queue"

    queue_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("canonical_event.event_id"), nullable=False)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entity.entity_id"), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProbeRun(Base):
    __tablename__ = "probe_run"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    probe_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    backend_id: Mapped[str] = mapped_column(Text, nullable=False)
    acquisition_mode: Mapped[str | None] = mapped_column(Text)
    success: Mapped[int] = mapped_column(Integer, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProbeFailure(Base):
    __tablename__ = "probe_failure"

    failure_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    probe_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProbeHealthRow(Base):
    __tablename__ = "probe_health"

    probe_id: Mapped[str] = mapped_column(Text, primary_key=True)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_ratio_24h: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False, default=0)
    mean_lag_seconds: Mapped[float | None] = mapped_column(Numeric(12, 3))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="down")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestLog(Base):
    __tablename__ = "ingest_log"

    log_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    probe_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertRow(Base):
    __tablename__ = "alert"

    alert_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("canonical_event.event_id"), nullable=False)
    priority: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    trigger_rules: Mapped[list] = mapped_column(JSON, nullable=False)
    calibrated_confidence: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    abstain: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    signal_urls: Mapped[list] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BriefRow(Base):
    __tablename__ = "brief"

    brief_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    model_assisted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelExchange(Base):
    __tablename__ = "model_exchange"

    exchange_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Sku(Base):
    """決策對象。同一 SKU 的各平台頁面不是同一規範事件的多條提及。"""

    __tablename__ = "sku"
    __table_args__ = (UniqueConstraint("code", name="uq_sku_code"),)

    sku_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkuOffer(Base):
    """頁面可觀察的標價與優惠原文。求解後的實付不寫在這張表。"""

    __tablename__ = "sku_offer"
    __table_args__ = (
        CheckConstraint("price_basis IN ('guest','logged_in')", name="ck_offer_basis"),
        CheckConstraint("acquisition_mode IN ('live','fallback')", name="ck_offer_mode"),
    )

    offer_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sku_id: Mapped[str] = mapped_column(ForeignKey("sku.sku_id"), nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    upstream_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acquisition_mode: Mapped[str] = mapped_column(Text, nullable=False)
    list_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    price_basis: Mapped[str] = mapped_column(Text, nullable=False)
    match_confidence: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    page_list_price_dropped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    protection_cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    inventory_reliability: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    after_sales: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    adjustments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    valid_until: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PriceObservation(Base):
    """慢速路徑累積的歷史標價。天數不足時決策必須標註置信度降低。"""

    __tablename__ = "price_observation"
    __table_args__ = (
        UniqueConstraint("sku_id", "observed_on", "source_url", name="uq_price_observation"),
    )

    observation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sku_id: Mapped[str] = mapped_column(ForeignKey("sku.sku_id"), nullable=False)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    list_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PriceDecision(Base):
    """研判層。不引用 canonical_event，也不回寫報價事實。"""

    __tablename__ = "price_decision"

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sku_id: Mapped[str] = mapped_column(ForeignKey("sku.sku_id"), nullable=False)
    scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    node: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
