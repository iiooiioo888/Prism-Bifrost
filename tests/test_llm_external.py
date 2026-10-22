"""外部模型未設定時不連外。接上之後仍不能寫金額或事實層。"""

import json
from pathlib import Path

import pytest

from prism.config import load_settings, reset_settings_cache
from prism.demo.commerce import SKU_CODE, seed_commerce
from prism.llm.assist import explain_decision, external_report
from prism.llm.external import ExternalLanguageModel, ExternalModelConfig, LlmConfigError, RateLimited, validate_base_url
from prism.llm.gateway import ModelGateway
from sqlalchemy import func, select

from prism.db.models import CanonicalEvent
from prism.db.session import session_scope


def _silence(monkeypatch) -> None:
    monkeypatch.delenv("PRISM_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("PRISM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("PRISM_LLM_MODEL", raising=False)


def _enable(monkeypatch, url: str = "https://models.example/v1") -> None:
    monkeypatch.setenv("PRISM_LLM_BASE_URL", url)
    monkeypatch.setenv("PRISM_LLM_API_KEY", "sk-test-secret")
    monkeypatch.setenv("PRISM_LLM_MODEL", "demo-model")


def _answer(offer_id: str, explanation: str, drivers: list[str] | None = None) -> str:
    return json.dumps(
        {"explanation": explanation, "drivers": drivers if drivers is not None else [offer_id], "speculative": False},
        ensure_ascii=False,
    )


def test_unconfigured_model_does_not_call_out(runtime, monkeypatch):
    _silence(monkeypatch)
    seen: list[str] = []
    runtime.llm_transport = lambda *_args: seen.append("called")
    assert external_report(runtime)["configured"] is False
    result = explain_decision(runtime, scene_id="double11", sku_code=SKU_CODE)
    assert result["status"] == "not_configured"
    assert result["event_created"] is False
    assert seen == []


def test_external_model_explains_without_sending_prices_or_the_key(runtime, monkeypatch):
    _enable(monkeypatch)
    seed_commerce(runtime)
    seen: list[tuple[str, dict, dict]] = []

    def transport(url, headers, body):
        seen.append((url, headers, body))
        prompt = json.loads(body["messages"][1]["content"])
        offer_id = prompt["offers"][0]["offer_id"]
        assert "payable" not in body["messages"][1]["content"]
        assert "198.00" not in body["messages"][1]["content"]
        content = _answer(offer_id, "排序靠前的方案要同時看價保與庫存可靠性，金額以決策表為準。")
        return 200, {"choices": [{"message": {"content": f"```json\n{content}\n```"}}]}

    runtime.llm_transport = transport
    with session_scope(runtime.factory) as session:
        before = int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)
    result = explain_decision(runtime, scene_id="double11", sku_code=SKU_CODE)
    assert result["status"] == "ok"
    assert result["model"] == "demo-model"
    assert result["explanation"]
    assert result["drivers"]
    assert "sk-test-secret" not in json.dumps(result, ensure_ascii=False)
    assert seen[0][0] == "https://models.example/v1/chat/completions"
    assert seen[0][1]["Authorization"] == "Bearer sk-test-secret"
    again = explain_decision(runtime, scene_id="double11", sku_code=SKU_CODE)
    assert again["explanation"] == result["explanation"]
    assert len(seen) == 1
    with session_scope(runtime.factory) as session:
        assert int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0) == before


def test_money_banned_claims_and_unknown_citations_are_not_shown(runtime, monkeypatch):
    _enable(monkeypatch)
    seed_commerce(runtime)

    def money(_url, _headers, _body):
        return 200, {"choices": [{"message": {"content": _answer("ignored", "實付只要 12 元")}}]}

    runtime.llm_transport = money
    rejected = explain_decision(runtime, scene_id="double11", sku_code=SKU_CODE)
    assert rejected["status"] == "rejected"
    assert rejected["explanation"] is None
    assert "12" not in json.dumps(rejected, ensure_ascii=False)

    runtime._model_gateway = None
    runtime._external_model = None

    def banned(_url, _headers, body):
        prompt = json.loads(body["messages"][1]["content"])
        return 200, {"choices": [{"message": {"content": _answer(prompt["offers"][0]["offer_id"], "這是全網最低的選擇")}}]}

    runtime.llm_transport = banned
    hidden = explain_decision(runtime, scene_id="double11", sku_code=SKU_CODE)
    assert hidden["status"] == "rejected"
    assert hidden["explanation"] is None
    assert "全網最低" not in json.dumps(hidden, ensure_ascii=False)

    runtime._model_gateway = None
    runtime._external_model = None

    def unknown(_url, _headers, _body):
        return 200, {"choices": [{"message": {"content": _answer("missing-offer", "引用不存在", ["missing-offer"])}}]}

    runtime.llm_transport = unknown
    loose = explain_decision(runtime, scene_id="double11", sku_code=SKU_CODE)
    assert loose["status"] == "unsupported"
    assert loose["explanation"] is None


def test_redirect_budget_and_rate_limit_stop_before_a_result(runtime, monkeypatch):
    _enable(monkeypatch)
    seed_commerce(runtime)
    runtime.llm_transport = lambda *_args: (302, {})
    redirected = explain_decision(runtime, scene_id="double11", sku_code=SKU_CODE)
    assert redirected["status"] == "invalid"
    assert "重新導向" in redirected["detail"]

    runtime._external_model = None
    runtime._model_gateway = ModelGateway(daily_token_budget=1)
    runtime._model_gateway.tokens_used = 1
    called = {"n": 0}

    def count(*_args):
        called["n"] += 1
        return 200, {"choices": [{"message": {"content": "{}"}}]}

    runtime.llm_transport = count
    blocked = explain_decision(runtime, scene_id="double11", sku_code=SKU_CODE)
    assert blocked["status"] == "budget_exceeded"
    assert called["n"] == 0

    model = ExternalLanguageModel(
        ExternalModelConfig("https://models.example/v1", "sk-test-secret", "demo-model"),
        lambda *_args: (200, {"choices": [{"message": {"content": "{}"}}]}),
        per_minute=1,
    )
    from datetime import datetime, timezone

    moment = datetime(2026, 10, 22, tzinfo=timezone.utc)
    model.complete({"task": "explain"}, now=moment)
    with pytest.raises(RateLimited):
        model.complete({"task": "again"}, now=moment)


def test_address_and_config_file_secrets_are_refused(tmp_path, monkeypatch):
    with pytest.raises(LlmConfigError, match="https"):
        validate_base_url("http://models.example/v1")
    with pytest.raises(LlmConfigError, match="不被接受"):
        validate_base_url("https://169.254.169.254/latest")
    assert validate_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
    source = Path("config")
    for name in ("prism.yaml", "raven.yaml", "site_rules.yaml", "scenes.yaml", "proxies.yaml"):
        text = (source / name).read_text(encoding="utf-8")
        if name == "prism.yaml":
            text = text.replace("gateway: openai-compatible", "gateway: openai-compatible\n  api_key: sk-file")
        (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setenv("PRISM_CONFIG", str(tmp_path / "prism.yaml"))
    reset_settings_cache()
    with pytest.raises(LlmConfigError, match="設定檔"):
        load_settings()
    reset_settings_cache()


def test_explain_api_stays_quiet_until_configured(runtime, client, monkeypatch):
    _silence(monkeypatch)
    viewer = {"Authorization": "Bearer dev-viewer"}
    analyst = {"Authorization": "Bearer dev-analyst"}
    assert client.get("/api/v1/llm", headers=viewer).status_code == 200
    assert client.get("/api/v1/llm", headers=viewer).json()["configured"] is False
    refused = client.post("/api/v1/scenes/double11/explain", json={"sku_code": SKU_CODE}, headers=viewer)
    assert refused.status_code == 403
    quiet = client.post("/api/v1/scenes/double11/explain", json={"sku_code": SKU_CODE}, headers=analyst)
    assert quiet.status_code == 200
    assert quiet.json()["status"] == "not_configured"
    assert "sk-" not in quiet.text
