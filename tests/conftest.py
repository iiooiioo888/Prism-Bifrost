import os

import pytest


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISM_DATABASE_URL", f"sqlite:///{tmp_path / 'prism.db'}")
    monkeypatch.setenv("PRISM_OBJECT_STORE", str(tmp_path / "objects"))
    monkeypatch.setenv("PRISM_CONFIG", str(os.path.abspath("config/prism.yaml")))
    from prism.config import reset_settings_cache

    reset_settings_cache()
    from prism.runtime import PrismRuntime

    return PrismRuntime()


@pytest.fixture()
def client(runtime):
    from fastapi.testclient import TestClient
    from prism.api.app import create_app

    return TestClient(create_app(runtime))
