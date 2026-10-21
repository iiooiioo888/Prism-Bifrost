"""模型閘道。只產生研判層草稿，不寫事實層，也不寫事件指標。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from prism.llm.money import REJECTION, contains_prohibited_money
from prism.llm.validate import (
    ModelOutputInvalid,
    parse_json_with_retry,
    validate_explanation,
    validate_merge_suggestion,
    validate_narrative,
)


@dataclass
class GatewayResult:
    status: str
    payload: dict[str, Any]
    cached: bool = False


class ModelGateway:
    """可替換閘道。預設不連外，呼叫端傳入補全函式。"""

    def __init__(self, max_retries: int = 1, cache_ttl_hours: int = 24, daily_token_budget: int = 2_000_000) -> None:
        self.max_retries = max_retries
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.daily_token_budget = daily_token_budget
        self._cache: dict[str, tuple[datetime, str, dict[str, Any]]] = {}
        self.tokens_used = 0
        self.calls = 0

    def budget_exceeded(self) -> bool:
        return self.tokens_used > self.daily_token_budget

    def complete_json(
        self,
        purpose: str,
        prompt: dict[str, Any],
        complete: Callable[[dict[str, Any]], str],
        *,
        known_mention_ids: set[str],
        token_cost: int = 0,
    ) -> GatewayResult:
        digest = hashlib.sha256(json.dumps({"purpose": purpose, "prompt": prompt}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        cached = self._cache.get(digest)
        now = datetime.now(timezone.utc)
        if cached and now - cached[0] <= self.cache_ttl:
            return GatewayResult(cached[1], cached[2], cached=True)
        if self.tokens_used + token_cost > self.daily_token_budget:
            return GatewayResult("budget_exceeded", {"error": "日權杖預算已超過閾值"})

        def retry() -> str:
            return complete(prompt)

        raw = complete(prompt)
        self.calls += 1
        self.tokens_used += token_cost
        if contains_prohibited_money(raw):
            self._cache[digest] = (now, "rejected", dict(REJECTION))
            return GatewayResult("rejected", dict(REJECTION))
        try:
            payload = parse_json_with_retry(raw, retry if self.max_retries else None, self.max_retries)
        except ModelOutputInvalid as exc:
            result = {"error": str(exc), "status": "invalid"}
            return GatewayResult("invalid", result)
        if purpose == "merge":
            draft = validate_merge_suggestion(payload, known_mention_ids)
            normalized = {
                "same_event": draft.same_event,
                "confidence": draft.confidence,
                "reasoning": draft.reasoning,
                "supporting_mention_ids": draft.supporting_mention_ids,
                "contradicting_signals": draft.contradicting_signals,
                "unsupported": draft.unsupported,
                "source": "model",
                "decides_merge": False,
            }
        elif purpose == "narrative":
            draft = validate_narrative(payload, known_mention_ids)
            normalized = {
                "event_id": draft.event_id,
                "cluster_id": draft.cluster_id,
                "label": draft.label,
                "summary": draft.summary,
                "mention_ids": draft.mention_ids,
                "model": draft.model,
                "unsupported": draft.unsupported,
            }
        elif purpose == "explain":
            draft = validate_explanation(payload, known_mention_ids)
            normalized = {
                "explanation": draft.explanation,
                "drivers": draft.drivers,
                "speculative": draft.speculative,
                "unsupported": draft.unsupported,
            }
        else:
            raise ValueError(f"未知模型用途：{purpose}")
        self._cache[digest] = (now, "cached", normalized)
        status = "unsupported" if normalized.get("unsupported") else "ok"
        return GatewayResult(status, normalized)
