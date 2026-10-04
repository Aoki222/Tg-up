"""创建 session 的请求体。api_id / api_hash 不出现在这里，用当前进程里的 Telegram 凭据。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..adapters.session_login import LoginResult


class SessionStartBody(BaseModel):
    mode: str = Field(description="bot 或 user")
    bot_token: str = ""
    phone: str = ""
    bind_group: bool = False
    group_id: int | None = None
    force: bool = False


class SessionCodeBody(BaseModel):
    login_id: str
    code: str


class SessionPasswordBody(BaseModel):
    login_id: str
    password: str


def login_payload(result: LoginResult) -> dict:
    return {
        "done": result.done,
        "step": result.step,
        "login_id": result.login_id,
        "name": result.name,
        "username": result.username,
        "is_bot": result.is_bot,
        "group_ok": result.group_ok,
        "group_error": result.group_error,
        "message": result.message,
        "qr_url": result.qr_url,
        "qr_image": result.qr_image,
        "qr_expires_in": result.qr_expires_in,
    }
