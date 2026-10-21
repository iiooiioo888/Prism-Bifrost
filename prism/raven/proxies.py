"""出口代理池。傳輸失敗才換路；目標拒絕時停止，不換 IP 再試。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse


class ProxyConfigError(ValueError):
    """代理池配置不允許。"""


class TransportError(RuntimeError):
    """出口沒有把請求送到目標。"""


class AccessDenied(RuntimeError):
    """目標拒絕。不得改用其他 IP 再試。"""

    def __init__(self, proxy_id: str, status_code: int | None, detail: str) -> None:
        self.proxy_id = proxy_id
        self.status_code = status_code
        super().__init__(detail)


class ProxyPoolExhausted(RuntimeError):
    """全部出口都傳輸失敗。結果必須顯式失敗，不能當成空成功。"""


ACCESS_DENIED_STATUSES = frozenset({401, 403, 429})
TRANSPORT_STATUSES = frozenset({502, 504})
DENIAL_MARKERS = ("captcha", "驗證碼", "人機驗證", "請登入", "請登錄", "access denied")


@dataclass(frozen=True)
class ProxyEndpoint:
    proxy_id: str
    url: str


@dataclass(frozen=True)
class ProxyPoolConfig:
    enabled: bool
    retry_on_access_denied: bool
    transport_failure_limit: int
    proxies: tuple[ProxyEndpoint, ...]


@dataclass(frozen=True)
class ProxyAttempt:
    proxy_id: str
    outcome: str
    status_code: int | None
    detail: str


@dataclass(frozen=True)
class EgressResult:
    proxy_id: str
    status_code: int
    body: str
    attempts: tuple[ProxyAttempt, ...]


Transport = Callable[[str, str | None], Awaitable[tuple[int, str]]]


def load_proxy_pool(document: Mapping[str, Any]) -> ProxyPoolConfig:
    retry = bool(document.get("retry_on_access_denied", False))
    if retry:
        raise ProxyConfigError("目標拒絕時不得換 IP 再試")
    proxies: list[ProxyEndpoint] = []
    seen: set[str] = set()
    for item in document.get("proxies") or []:
        if not isinstance(item, Mapping):
            raise ProxyConfigError("代理條目必須是映射")
        proxy_id = str(item.get("id") or "").strip()
        url = str(item.get("url") or "").strip()
        if not proxy_id or proxy_id in seen:
            raise ProxyConfigError("代理識別必須存在且不可重複")
        _validate_proxy_url(url)
        seen.add(proxy_id)
        proxies.append(ProxyEndpoint(proxy_id, url))
    limit = int(document.get("transport_failure_limit") or 3)
    if limit < 1:
        raise ProxyConfigError("傳輸失敗上限至少為 1")
    return ProxyPoolConfig(
        enabled=bool(document.get("enabled", False)),
        retry_on_access_denied=False,
        transport_failure_limit=limit,
        proxies=tuple(proxies),
    )


def _validate_proxy_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ProxyConfigError("代理網址只能是不含帳密的 http 或 https")


def access_denied(status_code: int, body: str) -> bool:
    if status_code in ACCESS_DENIED_STATUSES:
        return True
    haystack = body.lower()
    return any(marker in haystack or marker in body for marker in DENIAL_MARKERS)


async def fetch_with_pool(
    url: str,
    pool: ProxyPoolConfig,
    *,
    assert_allowed: Callable[[str], None],
    transport: Transport,
    transport_failures: Mapping[str, int] | None = None,
) -> EgressResult:
    """先過站點規則，再選出口。拒絕回應立刻停止。"""
    assert_allowed(url)
    failures = transport_failures or {}
    if not pool.enabled:
        return await _direct(url, transport)
    usable = [item for item in pool.proxies if failures.get(item.proxy_id, 0) < pool.transport_failure_limit]
    if not usable:
        raise ProxyPoolExhausted("全部出口傳輸中斷")
    attempts: list[ProxyAttempt] = []
    for proxy in usable:
        try:
            status_code, body = await transport(url, proxy.url)
        except TransportError as exc:
            attempts.append(ProxyAttempt(proxy.proxy_id, "transport_failure", None, str(exc)))
            continue
        if status_code in TRANSPORT_STATUSES:
            attempts.append(ProxyAttempt(proxy.proxy_id, "transport_failure", status_code, "出口閘道失敗"))
            continue
        if access_denied(status_code, body):
            attempts.append(ProxyAttempt(proxy.proxy_id, "access_denied", status_code, "目標拒絕，已停止且不改用其他 IP"))
            raise AccessDenied(proxy.proxy_id, status_code, attempts[-1].detail)
        attempts.append(ProxyAttempt(proxy.proxy_id, "success", status_code, "出口可用"))
        return EgressResult(proxy.proxy_id, status_code, body, tuple(attempts))
    raise ProxyPoolExhausted("全部出口傳輸中斷：" + "; ".join(item.detail for item in attempts))


async def _direct(url: str, transport: Transport) -> EgressResult:
    try:
        status_code, body = await transport(url, None)
    except TransportError as exc:
        raise ProxyPoolExhausted(f"直連傳輸失敗：{exc}") from exc
    if access_denied(status_code, body):
        raise AccessDenied("direct", status_code, "目標拒絕，直連停止")
    return EgressResult("direct", status_code, body, (ProxyAttempt("direct", "success", status_code, "未使用代理"),))


def classify_proxy(
    *,
    enabled: bool,
    consecutive_transport_failures: int,
    last_outcome: str | None,
    transport_failure_limit: int,
) -> tuple[str, str]:
    if not enabled:
        return "down", "未啟用。目標拒絕時不會改用其他 IP"
    if last_outcome is None:
        return "down", "尚未使用"
    if consecutive_transport_failures >= transport_failure_limit:
        return "down", "連續傳輸失敗，此出口中斷"
    if last_outcome == "access_denied":
        return "degraded", "目標拒絕，已停止且沒有改用其他 IP"
    if last_outcome == "transport_failure":
        return "degraded", "最近一次傳輸失敗"
    return "healthy", "最近一次出口可用"


def consecutive_transport_failures(outcomes: list[str]) -> int:
    count = 0
    for outcome in reversed(outcomes):
        if outcome != "transport_failure":
            break
        count += 1
    return count


def proxy_rows(
    pool: ProxyPoolConfig,
    runs_by_proxy: dict[str, list[tuple[str, datetime | None]]],
) -> list[dict[str, Any]]:
    rows = []
    for proxy in pool.proxies:
        runs = runs_by_proxy.get(proxy.proxy_id, [])
        outcomes = [outcome for outcome, _moment in runs]
        last_outcome = outcomes[-1] if outcomes else None
        last_success = next((moment for outcome, moment in reversed(runs) if outcome == "success"), None)
        failures = consecutive_transport_failures(outcomes)
        status, detail = classify_proxy(
            enabled=pool.enabled,
            consecutive_transport_failures=failures,
            last_outcome=last_outcome,
            transport_failure_limit=pool.transport_failure_limit,
        )
        rows.append(
            {
                "proxy_id": proxy.proxy_id,
                "url": proxy.url,
                "enabled": pool.enabled,
                "status": status,
                "detail": detail,
                "consecutive_transport_failures": failures,
                "last_outcome": last_outcome,
                "last_success_at": last_success.isoformat() if last_success else None,
            }
        )
    return rows
