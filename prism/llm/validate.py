"""模型輸出的結構、引用與角色約束。模型置信度不決定歸併。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


class ModelOutputInvalid(ValueError):
    """結構化輸出解析失敗。"""


@dataclass
class MergeSuggestionDraft:
    same_event: bool
    confidence: str
    reasoning: str
    supporting_mention_ids: list[str]
    contradicting_signals: list[str]
    unsupported: bool
    source: str = "model"


@dataclass
class NarrativeDraft:
    event_id: str
    cluster_id: int
    label: str
    summary: str
    mention_ids: list[str]
    model: str
    unsupported: bool


@dataclass
class ExplanationDraft:
    explanation: str
    drivers: list[str]
    speculative: bool
    unsupported: bool


@dataclass
class ValidatedClaims:
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)


def parse_json_with_retry(raw: str, retry: Callable[[], str] | None, max_retries: int = 1) -> dict[str, Any]:
    """解析失敗重試一次，仍失敗則標記為模型輸出無效。"""
    attempts = [raw]
    if retry is not None and max_retries > 0:
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            attempts.append(retry())
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            payload = json.loads(attempt)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(payload, dict):
            last_error = ModelOutputInvalid("模型輸出必須是物件")
            continue
        return payload
    raise ModelOutputInvalid("模型輸出無效") from last_error


def _known(ids: list[str], catalog: set[str]) -> bool:
    return bool(ids) and all(item in catalog for item in ids)


def validate_merge_suggestion(payload: dict[str, Any], known_mention_ids: set[str]) -> MergeSuggestionDraft:
    supporting = [str(item) for item in payload.get("supporting_mention_ids") or []]
    unsupported = not _known(supporting, known_mention_ids)
    return MergeSuggestionDraft(
        same_event=bool(payload.get("same_event")),
        confidence=str(payload.get("confidence") or "low"),
        reasoning=str(payload.get("reasoning") or ""),
        supporting_mention_ids=supporting,
        contradicting_signals=[str(item) for item in payload.get("contradicting_signals") or []],
        unsupported=unsupported,
    )


def validate_narrative(payload: dict[str, Any], known_mention_ids: set[str]) -> NarrativeDraft:
    mention_ids = [str(item) for item in payload.get("mention_ids") or []]
    return NarrativeDraft(
        event_id=str(payload.get("event_id") or ""),
        cluster_id=int(payload.get("cluster_id") or 0),
        label=str(payload.get("label") or ""),
        summary=str(payload.get("summary") or ""),
        mention_ids=mention_ids,
        model=str(payload.get("model") or ""),
        unsupported=not _known(mention_ids, known_mention_ids),
    )


def validate_explanation(payload: dict[str, Any], known_mention_ids: set[str]) -> ExplanationDraft:
    drivers = [str(item) for item in payload.get("drivers") or []]
    known = _known(drivers, known_mention_ids)
    speculative = bool(payload.get("speculative", not known))
    return ExplanationDraft(
        explanation=str(payload.get("explanation") or ""),
        drivers=drivers,
        speculative=speculative or not known,
        unsupported=not known,
    )


def discriminate(claims: list[dict[str, Any]], known_mention_ids: set[str], known_event_ids: set[str]) -> ValidatedClaims:
    """鑑別器：無引用、引用不存在或自相矛盾的證據不進入合成器。"""
    result = ValidatedClaims()
    for claim in claims:
        mention_ids = [str(item) for item in claim.get("mention_ids") or []]
        event_ids = [str(item) for item in claim.get("event_ids") or []]
        urls = [str(item) for item in claim.get("signal_urls") or [] if str(item).strip()]
        has_citation = bool(mention_ids or event_ids or urls)
        mentions_ok = all(item in known_mention_ids for item in mention_ids) if mention_ids else True
        events_ok = all(item in known_event_ids for item in event_ids) if event_ids else True
        if claim.get("contradicted"):
            result.rejected.append({**claim, "reason": "鑑別器否決：存在矛盾"})
            continue
        if not has_citation or not mentions_ok or not events_ok:
            result.rejected.append({**claim, "reason": "無支撐"})
            continue
        result.accepted.append(claim)
    return result


def unsupported_ratio(claims: list[dict[str, Any]], known_mention_ids: set[str], known_event_ids: set[str]) -> float:
    if not claims:
        return 0.0
    judged = discriminate(claims, known_mention_ids, known_event_ids)
    return len(judged.rejected) / len(claims)
