"""請外部模型解釋既有決策。解釋不寫事實層，也不改金額。"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import func, select

from prism.db.models import CanonicalEvent, EventMention, PriceDecision, Sku
from prism.db.session import session_scope
from prism.llm.external import (
    ExternalModelError,
    LlmConfigError,
    RateLimited,
    default_transport,
    resolve_external_config,
)
from prism.llm.gateway import ModelGateway
from prism.runtime import PrismRuntime

LABEL = "模型輔助，待人工確認"
UNCONFIGURED = "外部模型未設定，沒有呼叫。位址與憑證只從環境變數讀取，不寫在設定檔。"
_AMOUNT = re.compile(r"\d+(?:\.\d+)?")
_BANNED = ("最便宜", "最優", "全網最低", "實時", "全自動")


def external_report(runtime: PrismRuntime) -> dict[str, Any]:
    try:
        config = resolve_external_config(runtime.settings.llm)
    except LlmConfigError as exc:
        return {"configured": False, "provider": "openai-compatible", "model": None, "detail": str(exc)}
    if config is None:
        return {"configured": False, "provider": "openai-compatible", "model": None, "detail": UNCONFIGURED}
    return {
        "configured": True,
        "provider": "openai-compatible",
        "model": config.model,
        "detail": "已接入 OpenAI 相容介面。模型只解釋既有決策，不寫金額，也不寫事實層。",
    }


def explain_decision(runtime: PrismRuntime, *, scene_id: str, sku_code: str) -> dict[str, Any]:
    report = external_report(runtime)
    before = _event_counts(runtime)
    if not report["configured"]:
        return _result(report, "not_configured", None, [], False, report["detail"])
    payload = _latest_payload(runtime, scene_id, sku_code)
    if payload is None:
        return _result(report, "not_ready", None, [], False, "還沒有可解釋的決策，沒有呼叫外部模型。")
    prompt, known_ids = _prompt(payload)
    model = _language_model(runtime)
    if model is None:
        return _result(report, "not_configured", None, [], False, UNCONFIGURED)
    gateway = _gateway(runtime)
    estimate = max(1, len(json.dumps(prompt, ensure_ascii=False)) // 4)

    def complete(body: dict[str, Any]) -> str:
        return model.complete(body)

    try:
        draft = gateway.complete_json(
            "explain",
            prompt,
            complete,
            known_mention_ids=known_ids,
            token_cost=estimate,
        )
    except RateLimited as exc:
        return _result(report, "rate_limited", None, [], False, str(exc))
    except ExternalModelError as exc:
        return _result(report, "invalid", None, [], False, str(exc))
    _assert_unchanged(runtime, before)
    if draft.status == "unsupported":
        return _result(report, "unsupported", None, [], True, "引用無法核對，沒有採用這段解釋")
    explanation = draft.payload.get("explanation") if draft.status == "ok" else None
    if isinstance(explanation, str) and any(word in explanation for word in _BANNED):
        return _result(report, "rejected", None, [], False, "模型輸出含被禁止的判斷，已拒絕且不回顯")
    drivers = [str(item) for item in draft.payload.get("drivers") or []] if draft.status == "ok" else []
    detail = draft.payload.get("error") if draft.status in {"rejected", "invalid", "budget_exceeded"} else LABEL
    return _result(
        report,
        draft.status,
        explanation if isinstance(explanation, str) else None,
        drivers,
        False,
        str(detail or LABEL),
    )


def _language_model(runtime: PrismRuntime):
    transport = getattr(runtime, "llm_transport", None) or default_transport
    config = resolve_external_config(runtime.settings.llm)
    if config is None:
        return None
    current = (config.base_url, config.model, config.api_key, transport)
    cached = getattr(runtime, "_external_model", None)
    if cached is not None and getattr(runtime, "_external_identity", None) == current:
        return cached
    from prism.llm.external import ExternalLanguageModel

    per_minute = int(runtime.settings.llm.get("per_worker_per_minute") or 30)
    model = ExternalLanguageModel(config, transport, per_minute=per_minute)
    runtime._external_model = model
    runtime._external_identity = current
    return model


def _gateway(runtime: PrismRuntime) -> ModelGateway:
    gateway = getattr(runtime, "_model_gateway", None)
    if gateway is None:
        budget = runtime.settings.llm.get("budget") or {}
        gateway = ModelGateway(
            max_retries=int(runtime.settings.llm.get("max_retries") or 1),
            cache_ttl_hours=int(runtime.settings.llm.get("cache_ttl_hours") or 24),
            daily_token_budget=int(budget.get("daily_tokens") or 2_000_000),
        )
        runtime._model_gateway = gateway
    return gateway


def _prompt(payload: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    offers = []
    known: set[str] = set()
    for item in list(payload.get("ranking") or []) + list(payload.get("withheld") or []):
        offer_id = str(item.get("offer_id") or "")
        if not offer_id:
            continue
        known.add(offer_id)
        offers.append(
            {
                "offer_id": offer_id,
                "platform": item.get("platform"),
                "match_band": item.get("match_band"),
                "price_protection": item.get("price_protection"),
                "price_basis": item.get("price_basis"),
                "source_url": item.get("source_url"),
                "withheld_reason": _scrub(str(item.get("reason") or "")) or None,
            }
        )
    notes = [_scrub(str(item)) for item in (payload.get("abstain") or {}).get("confirmations") or []]
    prompt = {
        "task": "explain",
        "sku_code": payload.get("sku_code"),
        "offers": offers,
        "confirmations": [item for item in notes if item],
        "instruction": "只根據 offer_id 解釋匹配分檔、價保與需確認事項。不要寫金額。",
    }
    return prompt, known


def _scrub(text: str) -> str:
    return _AMOUNT.sub("［數字］", text)


def _latest_payload(runtime: PrismRuntime, scene_id: str, sku_code: str) -> dict[str, Any] | None:
    with session_scope(runtime.factory) as session:
        sku = session.scalar(select(Sku).where(Sku.code == sku_code, Sku.scene_id == scene_id))
        if sku is None:
            return None
        row = session.scalar(
            select(PriceDecision)
            .where(PriceDecision.sku_id == sku.sku_id, PriceDecision.scene_id == scene_id)
            .order_by(PriceDecision.created_at.desc())
        )
        if row is None or not isinstance(row.payload, dict):
            return None
        return row.payload


def _event_counts(runtime: PrismRuntime) -> tuple[int, int]:
    with session_scope(runtime.factory) as session:
        events = int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)
        mentions = int(session.scalar(select(func.count()).select_from(EventMention)) or 0)
    return events, mentions


def _assert_unchanged(runtime: PrismRuntime, before: tuple[int, int]) -> None:
    if _event_counts(runtime) != before:
        raise ExternalModelError("模型解釋不應建立規範事件")


def _result(
    report: dict[str, Any],
    status: str,
    explanation: str | None,
    drivers: list[str],
    unsupported: bool,
    detail: str,
) -> dict[str, Any]:
    return {
        "configured": report["configured"],
        "provider": report["provider"],
        "model": report["model"],
        "status": status,
        "label": LABEL,
        "explanation": explanation,
        "drivers": drivers,
        "unsupported": unsupported,
        "detail": detail,
        "event_created": False,
    }
