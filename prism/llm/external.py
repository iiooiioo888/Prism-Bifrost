"""OpenAI 相容的外部模型。未設定位址與憑證時不連外。"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
_BLOCKED_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_SECRET_KEYS = frozenset({"api_key", "apikey", "token", "secret", "authorization"})
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

Transport = Callable[[str, dict[str, str], dict[str, Any]], tuple[int, dict[str, Any]]]


class LlmConfigError(ValueError):
    """外部模型的設定不能採用。"""


class ExternalModelError(RuntimeError):
    """這次沒有可用的模型輸出。"""


class RateLimited(ExternalModelError):
    """這一分鐘的呼叫次數已滿。"""


@dataclass(frozen=True)
class ExternalModelConfig:
    base_url: str
    api_key: str
    model: str


def assert_no_llm_secret(document: dict[str, Any]) -> None:
    """憑證只走環境變數。設定檔裡出現憑證欄位就拒絕載入。"""
    section = document.get("llm") or {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in _SECRET_KEYS:
                    raise LlmConfigError("模型憑證不能寫在設定檔，請改用環境變數")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(section)


def validate_base_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.username or parsed.password or not host:
        raise LlmConfigError("模型位址不能帶帳密")
    if host in _BLOCKED_HOSTS:
        raise LlmConfigError("模型位址不被接受")
    if parsed.scheme == "http" and host in _LOCAL_HOSTS:
        return url.strip().rstrip("/")
    if parsed.scheme == "https":
        return url.strip().rstrip("/")
    raise LlmConfigError("外部模型只接受 https，本機 http 除外")


def resolve_external_config(settings_llm: dict[str, Any]) -> ExternalModelConfig | None:
    base_url = os.environ.get("PRISM_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("PRISM_LLM_API_KEY", "").strip()
    if not base_url and not api_key:
        return None
    if not base_url or not api_key:
        raise LlmConfigError("外部模型要同時提供位址與憑證，缺一則不呼叫")
    model = os.environ.get("PRISM_LLM_MODEL", "").strip() or str(settings_llm.get("model_brief") or "gpt-4o")
    return ExternalModelConfig(validate_base_url(base_url), api_key, model)


class ExternalLanguageModel:
    def __init__(self, config: ExternalModelConfig, transport: Transport, *, per_minute: int) -> None:
        self.config = config
        self.transport = transport
        self.per_minute = per_minute
        self._calls: list[datetime] = []

    def complete(self, prompt: dict[str, Any], now: datetime | None = None) -> str:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        cutoff = moment - timedelta(minutes=1)
        self._calls = [item for item in self._calls if item >= cutoff]
        if len(self._calls) >= self.per_minute:
            raise RateLimited("外部模型這一分鐘的呼叫次數已滿，沒有呼叫")
        self._calls.append(moment)
        url = f"{self.config.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是稜鏡的解釋器。只輸出一個 JSON 物件，欄位為 explanation、drivers、speculative。"
                        "drivers 只能使用使用者提供的 offer_id。"
                        "不要寫金額、貨幣符號、元、圓、RMB 或 CNY，也不要判斷最便宜、最優、全網最低、實時或全自動。"
                    ),
                },
                {"role": "user", "content": _user_content(prompt)},
            ],
        }
        try:
            status, payload = self.transport(url, headers, body)
        except ExternalModelError:
            raise
        except Exception as exc:
            raise ExternalModelError(_safe_error(str(exc), self.config.api_key)) from exc
        if status in {301, 302, 303, 307, 308}:
            raise ExternalModelError("外部模型回應重新導向，沒有跟隨")
        if status != 200:
            raise ExternalModelError(f"外部模型回應 {status}，沒有採用輸出")
        return _content(payload)


def default_transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False, trust_env=False) as client:
            response = client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise ExternalModelError("外部模型連線失敗，沒有採用輸出") from exc
    if response.status_code in {301, 302, 303, 307, 308}:
        return response.status_code, {}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload if isinstance(payload, dict) else {}


def _user_content(prompt: dict[str, Any]) -> str:
    import json

    return json.dumps(prompt, ensure_ascii=False, sort_keys=True)


def _content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ExternalModelError("外部模型沒有可用的結構化輸出")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ExternalModelError("外部模型沒有可用的結構化輸出")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ExternalModelError("外部模型沒有可用的結構化輸出")
    return _FENCE.sub("", content.strip())


def _safe_error(text: str, secret: str) -> str:
    redacted = text.replace(secret, "[redacted]") if secret else text
    if "http" in redacted.lower() or secret and secret in text:
        return "外部模型連線失敗，沒有採用輸出"
    return "外部模型連線失敗，沒有採用輸出"
