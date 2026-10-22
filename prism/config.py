"""讀取 config/prism.yaml、config/raven.yaml 與站點規則。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from prism.llm.external import assert_no_llm_secret
from prism.raven.proxies import ProxyPoolConfig, load_proxy_pool
from prism.scenes.registry import Scene, load_scenes

ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置必須是映射：{path}")
    return data


@dataclass(frozen=True)
class Settings:
    root: Path
    app: dict[str, Any]
    auth: dict[str, Any]
    raven: dict[str, Any]
    bridge: dict[str, Any]
    weave: dict[str, Any]
    watch: dict[str, Any]
    llm: dict[str, Any]
    herald: dict[str, Any]
    horn: dict[str, Any]
    raven_routes: dict[str, Any]
    site_rules: dict[str, Any]
    scenes: dict[str, Scene]
    proxies: ProxyPoolConfig
    official: dict[str, Any]

    @property
    def timezone(self) -> str:
        return str(self.app.get("timezone", "Asia/Shanghai"))

    @property
    def database_url(self) -> str:
        override = os.environ.get("PRISM_DATABASE_URL")
        if override:
            return override
        raw = str(self.app.get("database_url", "sqlite:///var/prism.db"))
        if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
            relative = raw.removeprefix("sqlite:///")
            return f"sqlite:///{(self.root / relative).resolve()}"
        return raw

    @property
    def object_store(self) -> Path:
        override = os.environ.get("PRISM_OBJECT_STORE")
        if override:
            return Path(override)
        return (self.root / str(self.app.get("object_store", "var/objects"))).resolve()

    def token_map(self) -> dict[str, dict[str, str]]:
        mapping: dict[str, dict[str, str]] = {}
        for item in self.auth.get("tokens", []):
            mapping[str(item["token"])] = {
                "role": str(item["role"]),
                "actor": str(item["actor"]),
            }
        return mapping


def load_settings(config_path: Path | None = None) -> Settings:
    root = ROOT
    prism_path = config_path or Path(os.environ.get("PRISM_CONFIG", root / "config" / "prism.yaml"))
    if not prism_path.is_absolute():
        prism_path = (root / prism_path).resolve()
    config_dir = prism_path.parent
    app_config = _load_yaml(prism_path)
    assert_no_llm_secret(app_config)
    return Settings(
        root=root,
        app=app_config.get("app", {}),
        auth=app_config.get("auth", {}),
        raven=app_config.get("raven", {}),
        bridge=app_config.get("bridge", {}),
        weave=app_config.get("weave", {}),
        watch=app_config.get("watch", {}),
        llm=app_config.get("llm", {}),
        herald=app_config.get("herald", {}),
        horn=app_config.get("horn", {}),
        raven_routes=_load_yaml(config_dir / "raven.yaml"),
        site_rules=_load_yaml(config_dir / "site_rules.yaml"),
        scenes=load_scenes(_load_yaml(config_dir / "scenes.yaml")),
        proxies=load_proxy_pool(_load_yaml(config_dir / "proxies.yaml")),
        official=app_config.get("official", {}) if isinstance(app_config.get("official", {}), dict) else {},
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
