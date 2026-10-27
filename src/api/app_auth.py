"""接口鉴权。读 Cookie 里的 session，不再使用 API_TOKEN。

must_change_password 为真时，只放行状态、当前用户、退出和改密。
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..adapters.admin_auth import AdminSession, cookie_from, load_session

PUBLIC_API = {
    ("GET", "/api/health"),
    ("GET", "/api/system/version"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
}

# 已登录但还没改掉初始密码时，只允许这些。
PASSWORD_GATE = {
    ("GET", "/api/auth/status"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/password"),
}


def is_public_api(method: str, path: str) -> bool:
    return (method.upper(), path) in PUBLIC_API


def password_change_required(session: AdminSession, method: str, path: str) -> bool:
    if not session.must_change_password:
        return False
    return (method.upper(), path) not in PASSWORD_GATE


async def require_token(request: Request) -> None:
    """名字沿用 require_token，避免改遍现有 Depends。实现改为校验 session。"""
    session = await load_session(cookie_from(request))
    if session is None:
        raise HTTPException(status_code=401, detail="需要登录")
    if password_change_required(session, request.method, request.url.path):
        raise HTTPException(status_code=403, detail="请先修改初始密码")
    request.state.admin_id = session.user_id
    request.state.admin_username = session.username
    request.state.must_change_password = session.must_change_password
