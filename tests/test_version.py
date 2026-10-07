from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.adapters.progress import ProgressHub
from src.api.app import (
    _child_manifest_digest,
    _fetch_remote_release_sync,
    _label_revision,
    _version_cache,
    create_api,
)


def _route(app, path: str, method: str):
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and method in methods:
            return route.endpoint
    raise AssertionError(f"missing {method} {path}")


def _reset_cache() -> None:
    _version_cache["checked_at"] = 0.0
    _version_cache["data"] = None


def test_child_manifest_prefers_linux_amd64() -> None:
    digest = _child_manifest_digest(
        {
            "manifests": [
                {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
            ]
        }
    )
    assert digest == "sha256:amd"


def test_label_revision_reads_image_config() -> None:
    assert _label_revision({"config": {"Labels": {"org.opencontainers.image.revision": "abc1234"}}}) == "abc1234"
    assert _label_revision({"config": {}}) is None


def test_latest_channel_does_not_fall_back_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def ghcr(tag: str) -> None:
        calls.append(tag)
        return None

    def github(ref: str) -> None:
        calls.append(ref)
        return None

    monkeypatch.setattr("src.api.version._ghcr_revision", ghcr)
    monkeypatch.setattr("src.api.version._github_commit", github)
    assert _fetch_remote_release_sync("latest") is None
    assert calls == ["latest"]


def test_staging_channel_falls_back_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.api.version._ghcr_revision", lambda tag: None)
    monkeypatch.setattr(
        "src.api.version._github_commit",
        lambda ref: {"sha": "abc1234567890", "commit": {"message": "feat"}, "html_url": "https://example"}
        if ref == "main"
        else None,
    )
    data = _fetch_remote_release_sync("staging")
    assert data is not None
    assert data["sha"] == "abc1234567890"


async def test_version_local_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "local_dev")
    monkeypatch.delenv("APP_CHANNEL", raising=False)
    _reset_cache()

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    with patch("src.api.version._fetch_remote_release_sync", side_effect=RuntimeError("should not be called")):
        data = await endpoint()
        assert data["current_version"] == "dev"
        assert data["remote_version"] is None
        assert data["has_update"] is False
        assert data["channel"] == "latest"
        assert data["status"] == "dev"


async def test_version_has_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "1111111222222233333334444444555555566666")
    monkeypatch.setenv("APP_CHANNEL", "latest")
    _reset_cache()

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    mock_remote = {
        "sha": "9999999888888877777776666666555555544444",
        "commit": {"message": "fix: critical bug\n\nmore details"},
        "html_url": "https://github.com/Aoki222/Tg-up/commit/9999999888888877777776666666555555544444",
    }
    with patch("src.api.version._fetch_remote_release_sync", return_value=mock_remote) as fetch:
        data = await endpoint()
        fetch.assert_called_once_with("latest")
        assert data["current_version"] == "1111111"
        assert data["remote_version"] == "9999999"
        assert data["has_update"] is True
        assert data["status"] == "update"
        assert data["commit_message"] == "fix: critical bug"
        assert data["commit_url"] == mock_remote["html_url"]


async def test_version_staging_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "abcdef1234567890abcdef1234567890abcdef12"
    monkeypatch.setenv("APP_VERSION", sha)
    monkeypatch.setenv("APP_CHANNEL", "staging")
    _reset_cache()

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    mock_remote = {
        "sha": sha,
        "commit": {"message": "release 1.0"},
        "html_url": f"https://github.com/Aoki222/Tg-up/commit/{sha}",
    }
    with patch("src.api.version._fetch_remote_release_sync", return_value=mock_remote) as fetch:
        data = await endpoint()
        fetch.assert_called_once_with("staging")
        assert data["current_version"] == sha[:7]
        assert data["remote_version"] == sha[:7]
        assert data["has_update"] is False
        assert data["channel"] == "staging"
        assert data["status"] == "staging"


async def test_version_staging_with_update_has_update_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "1111111222222233333334444444555555566666")
    monkeypatch.setenv("APP_CHANNEL", "staging")
    _reset_cache()

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    mock_remote = {
        "sha": "9999999888888877777776666666555555544444",
        "commit": {"message": "new commit on main"},
        "html_url": "https://github.com/Aoki222/Tg-up/commit/9999999888888877777776666666555555544444",
    }
    with patch("src.api.version._fetch_remote_release_sync", return_value=mock_remote):
        data = await endpoint()
        assert data["has_update"] is True
        assert data["channel"] == "staging"
        assert data["status"] == "update"


async def test_version_up_to_date_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "abcdef1234567890abcdef1234567890abcdef12"
    monkeypatch.setenv("APP_VERSION", sha)
    monkeypatch.setenv("APP_CHANNEL", "latest")
    _reset_cache()

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    mock_remote = {
        "sha": sha,
        "commit": {"message": "release 1.0"},
        "html_url": f"https://github.com/Aoki222/Tg-up/commit/{sha}",
    }
    with patch("src.api.version._fetch_remote_release_sync", return_value=mock_remote):
        data = await endpoint()
        assert data["current_version"] == sha[:7]
        assert data["remote_version"] == sha[:7]
        assert data["has_update"] is False
        assert data["channel"] == "latest"
        assert data["status"] == "latest"


async def test_version_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "abcdef1234567890abcdef1234567890abcdef12")
    monkeypatch.setenv("APP_CHANNEL", "staging")
    _reset_cache()

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    with patch("src.api.version._fetch_remote_release_sync", return_value=None):
        data = await endpoint()
        assert data["current_version"] == "abcdef1"
        assert data["remote_version"] is None
        assert data["has_update"] is False
        assert data["channel"] == "staging"
        assert data["status"] == "staging"


async def test_version_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "abcdef1234567890abcdef1234567890abcdef12")
    _version_cache["checked_at"] = time.monotonic()
    cached = {
        "current_version": "abcdef1",
        "remote_version": "1234567",
        "has_update": True,
        "channel": "latest",
        "status": "update",
        "commit_message": "cached message",
        "commit_url": "",
    }
    _version_cache["data"] = cached

    app = create_api(ProgressHub())
    endpoint = _route(app, "/api/system/version", "GET")

    with patch("src.api.version._fetch_remote_release_sync", side_effect=RuntimeError("should not be called")):
        data = await endpoint()
        assert data["current_version"] == "abcdef1"
        assert data["has_update"] is True
        assert data["status"] == "update"
