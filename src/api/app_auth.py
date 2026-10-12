"""接口鉴权。API_TOKEN 为空时放行，便于本机开发。"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from ..config import API_TOKEN


def api_token_ok(authorization: str | None, access_token: str | None = None) -> bool:
    """只认 Authorization: Bearer。查询参数里的长期令牌不再放行。"""
    del access_token
    if not API_TOKEN:
        return True
    return authorization == f"Bearer {API_TOKEN}"


async def require_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """写接口的依赖。读接口由 create_api 里的中间件统一拦。"""
    if api_token_ok(authorization, request.query_params.get("access_token")):
        return
    raise HTTPException(status_code=401, detail="需要 Authorization: Bearer <API_TOKEN>")
