"""初始账号登录后必须改密，改完之前不能进其它接口。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.adapters.admin_auth import INITIAL_PASSWORD, INITIAL_USERNAME, ensure_initial_admin
from src.adapters.progress import ProgressHub
from src.api.app import create_api
from src.database.connection import close_pool, open_pool
from src.database.init import init_db


async def _call(
    app,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    cookie: str | None = None,
) -> tuple[int, dict | None, str | None]:
    payload = b"" if body is None else json.dumps(body).encode()
    headers = []
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    if cookie:
        headers.append((b"cookie", f"tgup_sid={cookie}".encode()))
    chunks = bytearray()
    status = {"code": 500}
    set_cookie: dict[str, str | None] = {"value": None}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
            for key, value in message.get("headers") or []:
                if key.lower() == b"set-cookie" and value.startswith(b"tgup_sid="):
                    raw = value.decode().split(";", 1)[0]
                    set_cookie["value"] = raw.split("=", 1)[1]
        elif message["type"] == "http.response.body":
            chunks.extend(message.get("body") or b"")

    await app(
        {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    parsed = json.loads(chunks.decode()) if chunks else None
    return status["code"], parsed, set_cookie["value"]


@pytest.fixture
async def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr("src.config.DATABASE_PATH", db_path)
    monkeypatch.setattr("src.database.connection.DATABASE_PATH", db_path)
    await close_pool()
    await open_pool(db_path)
    await init_db()
    await ensure_initial_admin()
    application = create_api(ProgressHub())
    yield application
    await close_pool()


async def test_initial_login_cannot_open_tasks(app) -> None:
    status, payload, cookie = await _call(
        app,
        "POST",
        "/api/auth/login",
        body={"username": INITIAL_USERNAME, "password": INITIAL_PASSWORD},
    )
    assert status == 200
    assert payload and payload["must_change_password"] is True
    assert cookie
    status, payload, _ = await _call(app, "GET", "/api/progress", cookie=cookie)
    assert status == 403
    assert payload == {"detail": "请先修改初始密码"}


async def test_password_change_opens_the_workbench(app) -> None:
    _, _, cookie = await _call(
        app,
        "POST",
        "/api/auth/login",
        body={"username": INITIAL_USERNAME, "password": INITIAL_PASSWORD},
    )
    status, payload, cookie2 = await _call(
        app,
        "POST",
        "/api/auth/password",
        cookie=cookie,
        body={
            "current_password": INITIAL_PASSWORD,
            "new_password": "a-new-secret",
            "new_password_again": "a-new-secret",
        },
    )
    assert status == 200
    assert payload == {"ok": True, "must_change_password": False, "username": INITIAL_USERNAME}
    assert cookie2 and cookie2 != cookie
    status, _payload, _ = await _call(app, "GET", "/api/progress", cookie=cookie)
    assert status == 401
    status, payload, _ = await _call(app, "GET", "/api/progress", cookie=cookie2)
    assert status == 200
    assert payload == {"items": []}


async def test_cannot_keep_the_initial_password(app) -> None:
    _, _, cookie = await _call(
        app,
        "POST",
        "/api/auth/login",
        body={"username": INITIAL_USERNAME, "password": INITIAL_PASSWORD},
    )
    status, _payload, _ = await _call(
        app,
        "POST",
        "/api/auth/password",
        cookie=cookie,
        body={
            "current_password": INITIAL_PASSWORD,
            "new_password": INITIAL_PASSWORD,
            "new_password_again": INITIAL_PASSWORD,
        },
    )
    assert status == 400


async def test_username_can_be_changed_after_login(app) -> None:
    _, _, cookie = await _call(
        app,
        "POST",
        "/api/auth/login",
        body={"username": INITIAL_USERNAME, "password": INITIAL_PASSWORD},
    )
    _, _, cookie = await _call(
        app,
        "POST",
        "/api/auth/password",
        cookie=cookie,
        body={
            "current_password": INITIAL_PASSWORD,
            "new_password": "a-new-secret",
            "new_password_again": "a-new-secret",
        },
    )
    status, payload, _ = await _call(
        app,
        "POST",
        "/api/auth/username",
        cookie=cookie,
        body={"current_password": "a-new-secret", "username": "keeper"},
    )
    assert status == 200
    assert payload == {"ok": True, "username": "keeper"}

    status, payload, _ = await _call(app, "GET", "/api/auth/me", cookie=cookie)
    assert status == 200
    assert payload == {"username": "keeper"}

    status, _, cookie2 = await _call(
        app,
        "POST",
        "/api/auth/login",
        body={"username": "keeper", "password": "a-new-secret"},
    )
    assert status == 200
    assert cookie2

    status, _, _ = await _call(
        app,
        "POST",
        "/api/auth/login",
        body={"username": INITIAL_USERNAME, "password": "a-new-secret"},
    )
    assert status == 401


async def test_first_password_change_can_rename(app) -> None:
    _, _, cookie = await _call(
        app,
        "POST",
        "/api/auth/login",
        body={"username": INITIAL_USERNAME, "password": INITIAL_PASSWORD},
    )
    status, payload, cookie = await _call(
        app,
        "POST",
        "/api/auth/password",
        cookie=cookie,
        body={
            "current_password": INITIAL_PASSWORD,
            "new_password": "a-new-secret",
            "new_password_again": "a-new-secret",
            "username": "keeper",
        },
    )
    assert status == 200
    assert payload == {"ok": True, "must_change_password": False, "username": "keeper"}
    status, payload, _ = await _call(app, "GET", "/api/auth/me", cookie=cookie)
    assert payload == {"username": "keeper"}
    status, _, _ = await _call(
        app,
        "POST",
        "/api/auth/login",
        body={"username": INITIAL_USERNAME, "password": "a-new-secret"},
    )
    assert status == 401
