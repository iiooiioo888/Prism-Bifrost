"""出口代理池。傳輸失敗才換路，目標拒絕時停止。"""

from __future__ import annotations

import asyncio

import pytest

from prism.raven.compliance import ComplianceError, SitePolicy
from prism.raven.proxies import (
    AccessDenied,
    ProxyConfigError,
    ProxyEndpoint,
    ProxyPoolConfig,
    ProxyPoolExhausted,
    TransportError,
    fetch_with_pool,
    load_proxy_pool,
    proxy_rows,
)


def _policy() -> SitePolicy:
    return SitePolicy.from_config(
        {
            "defaults": {"allow_fetch": False, "respect_robots": True},
            "blocked_path_fragments": ["/login", "/captcha"],
            "sites": [{"domain": "port-authority.example", "allow_fetch": True, "respect_robots": True}],
        }
    )


def _pool() -> ProxyPoolConfig:
    return ProxyPoolConfig(
        enabled=True,
        retry_on_access_denied=False,
        transport_failure_limit=3,
        proxies=(ProxyEndpoint("local-a", "http://127.0.0.1:8888"), ProxyEndpoint("local-b", "http://127.0.0.1:8889")),
    )


def test_transport_failure_moves_to_next_proxy():
    calls: list[str | None] = []

    async def transport(url: str, proxy: str | None) -> tuple[int, str]:
        calls.append(proxy)
        if proxy and proxy.endswith("8888"):
            raise TransportError("連線被拒")
        return 200, "公開公告"

    result = asyncio.run(
        fetch_with_pool(
            "https://port-authority.example/notices/1",
            _pool(),
            assert_allowed=_policy().assert_allowed,
            transport=transport,
        )
    )
    assert result.proxy_id == "local-b"
    assert [item.outcome for item in result.attempts] == ["transport_failure", "success"]
    assert calls == ["http://127.0.0.1:8888", "http://127.0.0.1:8889"]


def test_access_denied_does_not_try_another_proxy():
    calls: list[str | None] = []

    async def transport(url: str, proxy: str | None) -> tuple[int, str]:
        calls.append(proxy)
        return 403, "forbidden"

    with pytest.raises(AccessDenied):
        asyncio.run(
            fetch_with_pool(
                "https://port-authority.example/notices/1",
                _pool(),
                assert_allowed=_policy().assert_allowed,
                transport=transport,
            )
        )
    assert calls == ["http://127.0.0.1:8888"]


def test_captcha_page_stops_without_rotation():
    calls: list[str | None] = []

    async def transport(url: str, proxy: str | None) -> tuple[int, str]:
        calls.append(proxy)
        return 200, "請完成人機驗證後再繼續"

    with pytest.raises(AccessDenied):
        asyncio.run(
            fetch_with_pool(
                "https://port-authority.example/notices/1",
                _pool(),
                assert_allowed=_policy().assert_allowed,
                transport=transport,
            )
        )
    assert len(calls) == 1


def test_all_transport_failures_are_explicit():
    async def transport(url: str, proxy: str | None) -> tuple[int, str]:
        raise TransportError("逾時")

    with pytest.raises(ProxyPoolExhausted):
        asyncio.run(
            fetch_with_pool(
                "https://port-authority.example/notices/1",
                _pool(),
                assert_allowed=_policy().assert_allowed,
                transport=transport,
            )
        )


def test_site_policy_runs_before_any_proxy():
    called = {"n": 0}

    async def transport(url: str, proxy: str | None) -> tuple[int, str]:
        called["n"] += 1
        return 200, "不該送到"

    with pytest.raises(ComplianceError):
        asyncio.run(
            fetch_with_pool(
                "https://port-authority.example/login",
                _pool(),
                assert_allowed=_policy().assert_allowed,
                transport=transport,
            )
        )
    assert called["n"] == 0


def test_config_rejects_evasion_and_embedded_secrets():
    with pytest.raises(ProxyConfigError):
        load_proxy_pool({"enabled": True, "retry_on_access_denied": True, "proxies": []})
    with pytest.raises(ProxyConfigError):
        load_proxy_pool(
            {"enabled": True, "proxies": [{"id": "secret", "url": "http://user:pass@127.0.0.1:8888"}]}
        )


def test_disabled_pool_status_stays_explicit(runtime, client):
    body = client.get("/api/v1/health/proxies", headers={"Authorization": "Bearer dev-viewer"}).json()
    assert body["enabled"] is False
    assert body["retry_on_access_denied"] is False
    assert [item["proxy_id"] for item in body["items"]] == ["local-a", "local-b"]
    assert all(item["status"] == "down" for item in body["items"])
    assert all("不會改用其他 IP" in item["detail"] for item in body["items"])
    anonymous = client.get("/api/v1/health/proxies")
    assert anonymous.status_code == 401


def test_access_denied_marks_proxy_degraded_without_disabling_the_pool():
    pool = _pool()
    rows = proxy_rows(pool, {"local-a": [("access_denied", None)], "local-b": []})
    by_id = {item["proxy_id"]: item for item in rows}
    assert by_id["local-a"]["status"] == "degraded"
    assert "沒有改用其他 IP" in by_id["local-a"]["detail"]
    assert by_id["local-b"]["status"] == "down"
    assert by_id["local-a"]["consecutive_transport_failures"] == 0
