"""控制台账号：登录、退出、首次改密。"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...adapters import admin_auth
from ..app_auth import require_token

_LOGIN_WINDOW = 15 * 60
_LOGIN_MAX = 5
_attempts: dict[str, list[float]] = defaultdict(list)
_attempts_lock = Lock()


class LoginBody(BaseModel):
    username: str
    password: str = Field(default="")


class PasswordBody(BaseModel):
    current_password: str
    new_password: str
    new_password_again: str
    username: str | None = None


class UsernameBody(BaseModel):
    current_password: str
    username: str


def _too_many(ip: str) -> bool:
    now = time.monotonic()
    with _attempts_lock:
        stamps = [item for item in _attempts[ip] if now - item < _LOGIN_WINDOW]
        _attempts[ip] = stamps
        return len(stamps) >= _LOGIN_MAX


def _record_fail(ip: str) -> None:
    with _attempts_lock:
        _attempts[ip].append(time.monotonic())


def _clear_fails(ip: str) -> None:
    with _attempts_lock:
        _attempts.pop(ip, None)


def register(app: FastAPI) -> None:
    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> dict:
        row = await admin_auth.owner_row()
        session = await admin_auth.load_session(admin_auth.cookie_from(request))
        return {
            "owner_exists": row is not None,
            "authenticated": session is not None,
            "username": session.username if session else (row["username"] if row else None),
            "must_change_password": bool(session.must_change_password) if session else bool(row and row["must_change_password"]),
        }

    @app.post("/api/auth/login")
    async def login(payload: LoginBody, request: Request) -> JSONResponse:
        ip = admin_auth.client_ip(request) or "unknown"
        if _too_many(ip):
            raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")
        username = await admin_auth.verify_login(payload.username, payload.password)
        if username is None:
            _record_fail(ip)
            raise HTTPException(status_code=401, detail="用户名或密码不正确")
        _clear_fails(ip)
        raw = await admin_auth.issue_session(username, request)
        session = await admin_auth.load_session(raw)
        body = JSONResponse({
            "ok": True,
            "username": username,
            "must_change_password": bool(session and session.must_change_password),
        })
        admin_auth.attach_session_cookie(body, raw, request)
        return body

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        await admin_auth.revoke_session(admin_auth.cookie_from(request))
        body = JSONResponse({"ok": True})
        admin_auth.clear_session_cookie(body)
        return body

    @app.get("/api/auth/me", dependencies=[Depends(require_token)])
    async def me(request: Request) -> dict:
        return {"username": getattr(request.state, "admin_username", "")}

    @app.post("/api/auth/password", dependencies=[Depends(require_token)])
    async def password(payload: PasswordBody, request: Request) -> JSONResponse:
        if payload.new_password != payload.new_password_again:
            raise HTTPException(status_code=400, detail="两次密码不一致")
        try:
            name = await admin_auth.change_password(
                payload.current_password,
                payload.new_password,
                payload.username,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        raw = await admin_auth.issue_session(name, request)
        body = JSONResponse({"ok": True, "must_change_password": False, "username": name})
        admin_auth.attach_session_cookie(body, raw, request)
        return body

    @app.post("/api/auth/username", dependencies=[Depends(require_token)])
    async def username(payload: UsernameBody, request: Request) -> dict:
        try:
            name = await admin_auth.change_username(payload.current_password, payload.username)
        except FileNotFoundError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        request.state.admin_username = name
        return {"ok": True, "username": name}
