import json
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.adapters.progress import ProgressHub
from src.adapters.session_login import SessionLoginService
from src.adapters.sessions import SessionPool
from src.api.app import IdentityPayload, create_api
from src.domain.settings_hub import SettingsHub
from src.domain.telegram_credentials import (
    credentials_path,
    load_telegram_credentials,
    merge_submitted_hash,
    save_telegram_credentials,
)

_HASH = "abcdef0123456789abcdef"
_OTHER = "1234567890abcdef1234567890abcdef"


class _Client:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.disconnected = False

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    async def is_user_authorized(self) -> bool:
        return False


def test_missing_credentials_are_unconfigured(tmp_path: Path) -> None:
    creds = load_telegram_credentials(tmp_path, {})
    assert creds.source == "none"
    assert creds.configured is False
    assert creds.api_id == 0
    assert creds.api_hash == ""


def test_env_used_when_file_absent(tmp_path: Path) -> None:
    creds = load_telegram_credentials(tmp_path, {"API_ID": "4", "API_HASH": _HASH})
    assert creds.source == "env"
    assert creds.api_id == 4
    assert creds.api_hash == _HASH


def test_complete_file_beats_env(tmp_path: Path) -> None:
    save_telegram_credentials(tmp_path, 7, _HASH)
    creds = load_telegram_credentials(tmp_path, {"API_ID": "1", "API_HASH": _OTHER})
    assert creds.source == "file"
    assert creds.api_id == 7
    assert creds.api_hash == _HASH
    assert not (tmp_path / "data" / "telegram.json.tmp").exists()


def test_incomplete_or_broken_file_falls_back_to_env(tmp_path: Path) -> None:
    path = credentials_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"api_id": 0, "api_hash": ""}', encoding="utf-8")
    creds = load_telegram_credentials(tmp_path, {"API_ID": "8", "API_HASH": _HASH})
    assert creds.source == "env"
    assert creds.api_id == 8

    path.write_text("{", encoding="utf-8")
    broken = load_telegram_credentials(tmp_path, {"API_ID": "8", "API_HASH": _HASH})
    assert broken.source == "env"


def test_blank_or_masked_hash_keeps_current() -> None:
    assert merge_submitted_hash("", _HASH) == _HASH
    assert merge_submitted_hash("abcd••••ef", _HASH) == _HASH
    assert merge_submitted_hash(_OTHER, _HASH) == _OTHER
    with pytest.raises(ValueError, match="API_HASH 长度不足"):
        merge_submitted_hash("short", _HASH)
    with pytest.raises(ValueError, match="API_HASH 长度不足"):
        merge_submitted_hash("", "")


def test_upload_settings_save_leaves_telegram_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    secret = "telegram-secret-hash"
    save_telegram_credentials(tmp_path, 9, secret)
    before = credentials_path(tmp_path).read_bytes()
    config = tmp_path / "upload.toml"
    config.write_text(
        'chat_id = -100\nobserver_paths = ["download"]\npreview = "off"\n',
        encoding="utf-8",
    )
    hub = SettingsHub(config, tmp_path)
    saved = hub.save_from_payload(
        {
            "chat_id": -100,
            "observer_paths": [str(tmp_path / "download")],
            "preview": "off",
            "topic_creation_enabled": True,
            "after_success": "keep",
            "max_retries": 3,
            "upload_timeout_seconds": 1200,
            "assigned_timeout_seconds": 600,
            "stable_timeout_seconds": 30,
            "watch_extensions": ["mp4"],
            "routes": [],
            "chats": [],
            "drive_folders": [],
        }
    )
    assert credentials_path(tmp_path).read_bytes() == before
    assert secret not in config.read_text(encoding="utf-8")
    assert "api_hash" not in saved
    assert secret not in json.dumps(saved)


def _route(app, path: str, method: str):
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and method in methods:
            return route.endpoint
    raise AssertionError(f"missing {method} {path}")


async def test_identity_hides_hash_and_keeps_masked_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.api.app.PROJECT_DIR", tmp_path)
    save_telegram_credentials(tmp_path, 5, _HASH)
    app = create_api(ProgressHub())
    get_identity = _route(app, "/api/identity", "GET")
    put_identity = _route(app, "/api/identity", "PUT")

    body = await get_identity()
    assert body["api_id"] == 5
    assert body["source"] == "file"
    assert body["configured"] is True
    assert body["api_hash_masked"] == "abcd••••ef"
    assert _HASH not in json.dumps(body)

    kept = await put_identity(IdentityPayload(api_id=6, api_hash=""))
    assert kept["restart_required"] is False
    assert kept["hot_reloaded"] is False
    assert kept["source"] == "file"
    assert load_telegram_credentials(tmp_path, {}).api_hash == _HASH
    assert load_telegram_credentials(tmp_path, {}).api_id == 6

    masked = await put_identity(IdentityPayload(api_id=6, api_hash=body["api_hash_masked"]))
    assert masked["ok"] is True
    assert load_telegram_credentials(tmp_path, {}).api_hash == _HASH

    with pytest.raises(HTTPException) as rejected:
        await put_identity(IdentityPayload(api_id=6, api_hash="short"))
    assert rejected.value.status_code == 400
    assert rejected.value.detail == "API_HASH 长度不足"
    assert load_telegram_credentials(tmp_path, {}).api_hash == _HASH

    seen: dict[str, int] = {}

    async def apply(creds) -> str:
        seen["api_id"] = creds.api_id
        return "same"

    app.state.apply_credentials = apply
    hot = await put_identity(IdentityPayload(api_id=11, api_hash=_OTHER))
    assert hot["hot_reloaded"] is True
    assert seen["api_id"] == 11
    assert load_telegram_credentials(tmp_path, {}).api_hash == _OTHER


async def test_sessions_report_live_api_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.api.app.PROJECT_DIR", tmp_path)
    app = create_api(ProgressHub())
    list_sessions = _route(app, "/api/sessions", "GET")
    app.state.session_login.replace_credentials(0, "")
    assert (await list_sessions())["api_configured"] is False
    app.state.session_login.replace_credentials(3, _HASH)
    assert (await list_sessions())["api_configured"] is True


async def test_login_requires_credentials(tmp_path: Path) -> None:
    service = SessionLoginService(tmp_path, 0, "")
    message = "请先在系统设置中配置 Telegram API_ID 与 API_HASH"
    with pytest.raises(ValueError, match=message):
        await service.start_bot("123:abc", None, False)
    with pytest.raises(ValueError, match=message):
        await service.start_user("+8613800138000", None, False)
    with pytest.raises(ValueError, match=message):
        await service.start_qr(None, False)


async def test_replace_same_keeps_client(tmp_path: Path) -> None:
    pool = SessionPool(tmp_path, 1, _HASH)
    client = _Client()
    pool.clients["bot"] = client
    assert await pool.replace_credentials(1, _HASH) == "same"
    assert client.disconnected is False
    assert pool.clients["bot"] is client


async def test_replace_disconnects_without_deleting_session(tmp_path: Path) -> None:
    pool = SessionPool(tmp_path, 1, _HASH)
    session = tmp_path / "bot.session"
    session.write_bytes(b"keep")
    client = _Client()
    pool.clients["bot"] = client
    assert await pool.replace_credentials(1, _OTHER) == "replaced"
    assert client.disconnected is True
    assert "bot" not in pool.clients
    assert session.read_bytes() == b"keep"
    assert pool._sessions_need_relogin is True


async def test_activate_does_not_mark_sessions_stale(tmp_path: Path) -> None:
    pool = SessionPool(tmp_path, 0, "")
    assert await pool.replace_credentials(3, _HASH) == "activated"
    assert pool.configured is True
    assert pool._sessions_need_relogin is False


async def test_unconfigured_pool_does_not_connect(tmp_path: Path) -> None:
    pool = SessionPool(tmp_path, 0, "")
    assert await pool.ensure_client("ghost", tmp_path / "ghost") is None
    assert pool.clients == {}


async def test_old_api_id_warning_logs_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    pool = SessionPool(tmp_path, 2, _HASH)
    pool._sessions_need_relogin = True
    pool.clients["old"] = _Client(connected=False)
    with caplog.at_level(logging.WARNING):
        assert await pool.ensure_client("old", tmp_path / "old") is None
        assert "该会话属于旧的 API_ID，需要重新登录" in caplog.text
        caplog.clear()
        assert await pool.ensure_client("old", tmp_path / "old") is None
        assert "旧的 API_ID" not in caplog.text
