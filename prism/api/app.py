"""稜鏡應用介面。事實層寫入只發生在顯式複核或高置信規則歸併。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from prism.api.deps import Principal, principal_from_request, require_confirm, require_role
from prism.clock import ensure_utc
from prism.db.models import (
    AlertRow,
    AnalysisNote,
    BriefRow,
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
from prism.db.session import session_scope
from prism.herald.brief import BriefService
from prism.herald.review import (
    ReviewError,
    accept_suggestion,
    erase_mentions,
    label_mention,
    merge_events,
    promote_event,
    reject_suggestion,
    rescore_after,
    split_event,
)
from prism.api.images import register_image_routes
from prism.api.scenes import register_scene_routes
from prism.runtime import PrismRuntime


class MergeBody(BaseModel):
    source_event_id: str
    reason: str | None = None


class SplitBody(BaseModel):
    mention_ids: list[str] = Field(min_length=1)
    reason: str | None = None
    title: str | None = None


class LabelBody(BaseModel):
    label: str
    note: str | None = None


class PromoteBody(BaseModel):
    reason: str | None = None


class SuggestionBody(BaseModel):
    reason: str | None = None


class BriefBody(BaseModel):
    period: str = "day"


class ErasureBody(BaseModel):
    source_url: str | None = None
    author: str | None = None
    reason: str | None = None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _num(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def create_app(runtime: PrismRuntime | None = None) -> FastAPI:
    application = FastAPI(title="稜鏡 Prism Bifröst", version="1.0.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.state.runtime = runtime or PrismRuntime()

    def runtime_of(request: Request) -> PrismRuntime:
        return request.app.state.runtime

    @application.get("/api/v1/health/probes")
    def probe_health(request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        return {"items": runtime_of(request).configured_probe_status()}

    @application.get("/api/v1/health/proxies")
    def proxy_health(request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        return runtime_of(request).configured_proxy_status()

    @application.get("/api/v1/events")
    def list_events(
        request: Request,
        status: str | None = None,
        min_priority: float | None = None,
        entity: str | None = None,
        abstain: bool | None = None,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "viewer")
        with session_scope(runtime_of(request).factory) as session:
            stmt = select(CanonicalEvent, EventAssessment).join(
                EventAssessment, EventAssessment.event_id == CanonicalEvent.event_id, isouter=True
            )
            if status:
                stmt = stmt.where(CanonicalEvent.status == status)
            if min_priority is not None:
                stmt = stmt.where(EventAssessment.priority >= min_priority)
            if abstain is not None:
                stmt = stmt.where(EventAssessment.abstain == (1 if abstain else 0))
            rows = session.execute(stmt.order_by(CanonicalEvent.last_updated.desc())).all()
            items = []
            for event, assessment in rows:
                names = _entity_names(session, event.event_id)
                if entity and entity not in names and not any(entity in name for name in names):
                    continue
                mention_count = len(
                    session.scalars(select(EventMention.mention_id).where(EventMention.event_id == event.event_id)).all()
                )
                items.append(_event_summary(event, assessment, mention_count, names))
        return {"items": items}

    @application.get("/api/v1/events/{event_id}")
    def get_event(event_id: str, request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        with session_scope(runtime_of(request).factory) as session:
            event = session.get(CanonicalEvent, event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="事件不存在")
            return _event_detail(session, event)

    @application.get("/api/v1/events/{event_id}/mentions")
    def get_mentions(event_id: str, request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        with session_scope(runtime_of(request).factory) as session:
            if session.get(CanonicalEvent, event_id) is None:
                raise HTTPException(status_code=404, detail="事件不存在")
            mentions = session.scalars(select(EventMention).where(EventMention.event_id == event_id)).all()
            return {"items": [_mention(session, mention) for mention in mentions]}

    @application.get("/api/v1/events/{event_id}/metrics")
    def get_metrics(event_id: str, request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        with session_scope(runtime_of(request).factory) as session:
            rows = session.scalars(select(EventMetrics).where(EventMetrics.event_id == event_id)).all()
            return {"items": [_metric(row) for row in rows]}

    @application.post("/api/v1/events/{event_id}/merge")
    def merge_route(
        event_id: str,
        body: MergeBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        require_confirm(request.headers.get("x-confirm"))
        try:
            with session_scope(runtime_of(request).factory) as session:
                merge_events(session, body.source_event_id, event_id, principal.actor, body.reason)
                rescore_after(session, runtime_of(request).settings, [event_id, body.source_event_id])
        except ReviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "merged", "event_id": event_id}

    @application.post("/api/v1/events/{event_id}/split")
    def split_route(
        event_id: str,
        body: SplitBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        require_confirm(request.headers.get("x-confirm"))
        try:
            with session_scope(runtime_of(request).factory) as session:
                created = split_event(session, event_id, body.mention_ids, principal.actor, body.reason, body.title)
                rescore_after(session, runtime_of(request).settings, [event_id, created.event_id])
                created_id = created.event_id
        except ReviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "split", "event_id": created_id}

    @application.post("/api/v1/events/{event_id}/promote")
    def promote_route(
        event_id: str,
        body: PromoteBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        require_confirm(request.headers.get("x-confirm"))
        try:
            with session_scope(runtime_of(request).factory) as session:
                promote_event(session, event_id, principal.actor, body.reason)
                rescore_after(session, runtime_of(request).settings, [event_id])
        except ReviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "promoted", "event_id": event_id}

    @application.post("/api/v1/mentions/{mention_id}/label")
    def label_route(
        mention_id: str,
        body: LabelBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        with session_scope(runtime_of(request).factory) as session:
            try:
                mention = session.get(EventMention, mention_id)
                if mention is None:
                    raise HTTPException(status_code=404, detail="提及不存在")
                label_mention(session, mention_id, body.label, principal.actor, body.note)
                if mention.event_id:
                    rescore_after(session, runtime_of(request).settings, [mention.event_id])
            except ReviewError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "labeled", "mention_id": mention_id}

    @application.get("/api/v1/alerts")
    def list_alerts(
        request: Request,
        since: datetime | None = None,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "viewer")
        with session_scope(runtime_of(request).factory) as session:
            stmt = select(AlertRow)
            if since is not None:
                stmt = stmt.where(AlertRow.created_at >= since)
            rows = session.scalars(stmt.order_by(AlertRow.created_at.desc())).all()
            return {
                "items": [
                    {
                        "alert_id": row.alert_id,
                        "event_id": row.event_id,
                        "priority": _num(row.priority),
                        "trigger_rules": row.trigger_rules,
                        "calibrated_confidence": _num(row.calibrated_confidence),
                        "abstain": bool(row.abstain),
                        "signal_urls": row.signal_urls,
                        "summary": row.summary,
                        "created_at": _iso(row.created_at),
                    }
                    for row in rows
                ]
            }

    @application.get("/api/v1/briefs")
    def list_briefs(request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        with session_scope(runtime_of(request).factory) as session:
            rows = session.scalars(select(BriefRow).order_by(BriefRow.created_at.desc())).all()
            return {"items": [_brief(row) for row in rows]}

    @application.post("/api/v1/briefs")
    def create_brief(
        body: BriefBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        with session_scope(runtime_of(request).factory) as session:
            row = BriefService(session, runtime_of(request).settings).generate(body.period)
            return _brief(row)

    @application.get("/api/v1/briefs/{brief_id}")
    def get_brief(brief_id: str, request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        with session_scope(runtime_of(request).factory) as session:
            row = session.get(BriefRow, brief_id)
            if row is None:
                raise HTTPException(status_code=404, detail="簡報不存在")
            return _brief(row)

    @application.get("/api/v1/review/queue")
    def review_queue(request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        with session_scope(runtime_of(request).factory) as session:
            suggestions = session.scalars(
                select(MergeSuggestion).where(MergeSuggestion.status == "pending").order_by(MergeSuggestion.created_at.desc())
            ).all()
            return {
                "suggestions": [
                    {
                        "suggestion_id": row.suggestion_id,
                        "left_event_id": row.left_event_id,
                        "right_event_id": row.right_event_id,
                        "left_mention_id": row.left_mention_id,
                        "right_mention_id": row.right_mention_id,
                        "score": _num(row.score),
                        "band": row.band,
                        "source": row.source,
                        "payload": row.payload,
                        "status": row.status,
                        "created_at": _iso(row.created_at),
                    }
                    for row in suggestions
                ]
            }

    @application.post("/api/v1/review/suggestions/{suggestion_id}/accept")
    def accept_route(
        suggestion_id: str,
        body: SuggestionBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        require_confirm(request.headers.get("x-confirm"))
        try:
            with session_scope(runtime_of(request).factory) as session:
                suggestion = session.get(MergeSuggestion, suggestion_id)
                if suggestion is None:
                    raise HTTPException(status_code=404, detail="歸併建議不存在")
                target = suggestion.left_event_id
                source = suggestion.right_event_id
                accept_suggestion(session, suggestion_id, principal.actor, body.reason)
                rescore_after(session, runtime_of(request).settings, [target, source])
        except ReviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "accepted", "suggestion_id": suggestion_id}

    @application.post("/api/v1/review/suggestions/{suggestion_id}/reject")
    def reject_route(
        suggestion_id: str,
        body: SuggestionBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "analyst")
        with session_scope(runtime_of(request).factory) as session:
            try:
                reject_suggestion(session, suggestion_id, principal.actor, body.reason)
            except ReviewError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "rejected", "suggestion_id": suggestion_id}

    @application.post("/api/v1/compliance/erasure")
    def erasure_route(
        body: ErasureBody,
        request: Request,
        principal: Principal = Depends(principal_from_request),
    ) -> dict:
        require_role(principal, "admin")
        require_confirm(request.headers.get("x-confirm"))
        try:
            with session_scope(runtime_of(request).factory) as session:
                removed = erase_mentions(
                    session,
                    actor=principal.actor,
                    source_url=body.source_url,
                    author=body.author,
                    reason=body.reason,
                )
        except ReviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"removed": removed}

    @application.get("/api/v1/dashboard")
    def dashboard(request: Request, principal: Principal = Depends(principal_from_request)) -> dict:
        require_role(principal, "viewer")
        moment = datetime.now(timezone.utc)
        with session_scope(runtime_of(request).factory) as session:
            mentions = session.scalars(select(EventMention)).all()
            series = []
            for hour in range(23, -1, -1):
                end = moment - timedelta(hours=hour)
                start = end - timedelta(hours=1)
                count = 0
                for mention in mentions:
                    stamp = mention.upstream_timestamp
                    if stamp is None:
                        continue
                    stamp = ensure_utc(stamp)
                    if start <= stamp < end:
                        count += 1
                series.append({"t": end.isoformat(), "count": count})
            assessments = session.scalars(select(EventAssessment)).all()
            ranking = []
            bursts = []
            histogram = {"0.00-0.35": 0, "0.35-0.60": 0, "0.60-0.85": 0, "0.85-1.00": 0}
            for assessment in assessments:
                event = session.get(CanonicalEvent, assessment.event_id)
                if event is None or event.status != "active":
                    continue
                entry = {
                    "event_id": event.event_id,
                    "title": event.title,
                    "priority": _num(assessment.priority),
                    "burst_score": _num(assessment.burst_score),
                    "abstain": bool(assessment.abstain),
                }
                if not assessment.abstain:
                    ranking.append(entry)
                if float(assessment.burst_score or 0) > 3 and not assessment.abstain:
                    bursts.append(entry)
                confidence = float(assessment.calibrated_confidence or 0)
                if confidence < 0.35:
                    histogram["0.00-0.35"] += 1
                elif confidence < 0.60:
                    histogram["0.35-0.60"] += 1
                elif confidence < 0.85:
                    histogram["0.60-0.85"] += 1
                else:
                    histogram["0.85-1.00"] += 1
            ranking.sort(key=lambda item: item["priority"] or 0, reverse=True)
            return {
                "mention_series": series,
                "confidence_histogram": histogram,
                "burst_events": bursts,
                "priority_ranking": ranking[:8],
                "probes": runtime_of(request).configured_probe_status(),
            }

    register_scene_routes(application)
    register_image_routes(application)
    return application


def _entity_names(session, event_id: str) -> list[str]:  # type: ignore[no-untyped-def]
    rows = session.execute(
        select(Entity.name).join(EventEntity, EventEntity.entity_id == Entity.entity_id).where(EventEntity.event_id == event_id)
    ).all()
    return [row[0] for row in rows]


def _event_summary(event: CanonicalEvent, assessment: EventAssessment | None, mention_count: int, names: list[str]) -> dict:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "title": event.title,
        "first_seen": _iso(event.first_seen),
        "last_updated": _iso(event.last_updated),
        "status": event.status,
        "priority": None if assessment is None else _num(assessment.priority),
        "calibrated_confidence": None if assessment is None else _num(assessment.calibrated_confidence),
        "abstain": bool(assessment.abstain) if assessment else False,
        "burst_score": None if assessment is None else _num(assessment.burst_score),
        "mention_count": mention_count,
        "entities": names,
        "promoted": bool(assessment.promoted) if assessment else False,
    }


def _mention(session, mention: EventMention) -> dict:  # type: ignore[no-untyped-def]
    labels = session.scalars(
        select(MentionLabel).where(MentionLabel.mention_id == mention.mention_id).order_by(MentionLabel.created_at.desc())
    ).all()
    notes = session.scalars(
        select(AnalysisNote).where(AnalysisNote.target_type == "event_mention", AnalysisNote.target_id == mention.mention_id)
    ).all()
    return {
        "mention_id": mention.mention_id,
        "event_id": mention.event_id,
        "source_url": mention.source_url,
        "upstream_timestamp": _iso(mention.upstream_timestamp),
        "collected_at": _iso(mention.collected_at),
        "platform": mention.platform,
        "author": mention.author,
        "title": mention.title,
        "body": mention.body,
        "language": mention.language,
        "acquisition_mode": mention.acquisition_mode,
        "raw_ref": mention.raw_ref,
        "content_hash": mention.content_hash,
        "duplicate_of": mention.duplicate_of,
        "near_duplicate": mention.duplicate_of is not None,
        "link_method": mention.link_method,
        "link_confidence": _num(mention.link_confidence),
        "source_type": mention.source_type,
        "labels": [{"label": row.label, "note": row.note, "actor": row.actor, "created_at": _iso(row.created_at)} for row in labels],
        "notes": [{"body": row.body, "actor": row.actor, "created_at": _iso(row.created_at)} for row in notes],
    }


def _metric(row: EventMetrics) -> dict:
    return {
        "event_id": row.event_id,
        "window_start": _iso(row.window_start),
        "window_end": _iso(row.window_end),
        "mention_count": row.mention_count,
        "source_diversity": _num(row.source_diversity),
        "platform_entropy": _num(row.platform_entropy),
        "diffusion_rate": _num(row.diffusion_rate),
        "sentiment_dist": row.sentiment_dist or {},
        "burst_score": _num(row.burst_score),
        "computed_at": _iso(row.computed_at),
    }


def _brief(row: BriefRow) -> dict:
    return {
        "brief_id": row.brief_id,
        "period": row.period,
        "body": row.body,
        "evidence": row.evidence,
        "model_assisted": bool(row.model_assisted),
        "created_at": _iso(row.created_at),
    }


def _event_detail(session, event: CanonicalEvent) -> dict:  # type: ignore[no-untyped-def]
    assessment = session.get(EventAssessment, event.event_id)
    mentions = session.scalars(select(EventMention).where(EventMention.event_id == event.event_id)).all()
    metrics = session.scalars(select(EventMetrics).where(EventMetrics.event_id == event.event_id)).all()
    suggestions = session.scalars(
        select(MergeSuggestion).where(
            or_(MergeSuggestion.left_event_id == event.event_id, MergeSuggestion.right_event_id == event.event_id)
        )
    ).all()
    narratives = session.scalars(select(EventNarrative).where(EventNarrative.event_id == event.event_id)).all()
    entities = session.execute(
        select(Entity, EventEntity)
        .join(EventEntity, EventEntity.entity_id == Entity.entity_id)
        .where(EventEntity.event_id == event.event_id)
    ).all()
    summary = _event_summary(event, assessment, len(mentions), [entity.name for entity, _link in entities])
    summary.update(
        {
            "merged_from": event.merged_from or [],
            "provenance": event.provenance or {},
            "components": {} if assessment is None else assessment.components,
            "mentions": [_mention(session, mention) for mention in mentions],
            "metrics": [_metric(row) for row in metrics],
            "suggestions": [
                {
                    "suggestion_id": row.suggestion_id,
                    "left_event_id": row.left_event_id,
                    "right_event_id": row.right_event_id,
                    "score": _num(row.score),
                    "band": row.band,
                    "source": row.source,
                    "status": row.status,
                    "payload": row.payload,
                }
                for row in suggestions
            ],
            "narratives": [
                {
                    "narrative_id": row.narrative_id,
                    "cluster_id": row.cluster_id,
                    "label": row.label,
                    "summary": row.summary,
                    "mention_ids": row.mention_ids,
                    "model": row.model,
                    "unsupported": bool(row.unsupported),
                }
                for row in narratives
            ],
            "entities": [
                {
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "role": link.role,
                    "confidence": _num(link.confidence),
                    "link_method": link.link_method,
                }
                for entity, link in entities
            ],
        }
    )
    return summary
