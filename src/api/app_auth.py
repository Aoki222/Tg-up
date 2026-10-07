"""接口鉴权。API_TOKEN 为空时放行，便于本机开发。"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from ..config import API_TOKEN


def api_token_ok(authorization: str | None, access_token: str | None) -> bool:
    """Bearer 头或 access_token 查询参数任一匹配即可。EventSource 不能带头，所以要查询参数。"""
    if not API_TOKEN:
        return True
    if authorization == f"Bearer {API_TOKEN}":
        return True
    return bool(access_token) and access_token == API_TOKEN


async def require_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """写接口的依赖。读接口由 create_api 里的中间件统一拦。"""
    if api_token_ok(authorization, request.query_params.get("access_token")):
        return
    raise HTTPException(status_code=401, detail="需要 Authorization: Bearer <API_TOKEN>")
