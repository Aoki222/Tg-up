from __future__ import annotations

import time
from unittest.mock import patch
import pytest

from src.adapters.progress import ProgressHub
from src.api.app import create_api, _version_cache


def _route(app, path: str, method: str):
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and method in methods:
            return route.endpoint
    raise AssertionError(f"missing {method} {path}")


async def test_version_local_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "local_dev")
    _version_cache["checked_at"] = 0.0
    _version_cache["data"] = None

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    with patch("src.api.app._fetch_github_commit_sync", return_value={"sha": "abc1234567890", "commit": {"message": "feat: test"}, "html_url": "https://github.com/..."}):
        data = await endpoint()
        assert data["current_version"] == "dev"
        assert data["remote_version"] == "abc1234"
        assert data["has_update"] is False


async def test_version_has_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "1111111222222233333334444444555555566666")
    _version_cache["checked_at"] = 0.0
    _version_cache["data"] = None

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    mock_remote = {
        "sha": "9999999888888877777776666666555555544444",
        "commit": {"message": "fix: critical bug\n\nmore details"},
        "html_url": "https://github.com/Aoki222/Tg-up/commit/9999999888888877777776666666555555544444",
    }
    with patch("src.api.app._fetch_github_commit_sync", return_value=mock_remote):
        data = await endpoint()
        assert data["current_version"] == "1111111"
        assert data["remote_version"] == "9999999"
        assert data["has_update"] is True
        assert data["commit_message"] == "fix: critical bug"
        assert data["commit_url"] == mock_remote["html_url"]


async def test_version_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "abcdef1234567890abcdef1234567890abcdef12"
    monkeypatch.setenv("APP_VERSION", sha)
    _version_cache["checked_at"] = 0.0
    _version_cache["data"] = None

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    mock_remote = {
        "sha": sha,
        "commit": {"message": "release 1.0"},
        "html_url": f"https://github.com/Aoki222/Tg-up/commit/{sha}",
    }
    with patch("src.api.app._fetch_github_commit_sync", return_value=mock_remote):
        data = await endpoint()
        assert data["current_version"] == sha[:7]
        assert data["remote_version"] == sha[:7]
        assert data["has_update"] is False


async def test_version_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "local_dev")
    _version_cache["checked_at"] = 0.0
    _version_cache["data"] = None

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    with patch("src.api.app._fetch_github_commit_sync", return_value=None):
        data = await endpoint()
        assert data["current_version"] == "dev"
        assert data["remote_version"] is None
        assert data["has_update"] is False


async def test_version_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "local_dev")
    _version_cache["checked_at"] = time.monotonic()
    cached = {
        "current_version": "cached_dev",
        "remote_version": "1234567",
        "has_update": True,
        "commit_message": "cached message",
        "commit_url": "",
    }
    _version_cache["data"] = cached

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    # 不应该调用 _fetch_github_commit_sync
    with patch("src.api.app._fetch_github_commit_sync", side_effect=RuntimeError("should not be called")):
        data = await endpoint()
        assert data["current_version"] == "cached_dev"
        assert data["has_update"] is True
