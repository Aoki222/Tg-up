"""群、频道和话题。路由只编排，Telegram 在 topic_admin，名单在数据库。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ...adapters.telegram.topics import TopicAdminError
from ..app_auth import require_token
from ..models import ChatIdBody


class TopicTitleBody(BaseModel):
    title: str = Field(min_length=1, max_length=128)


def register(app: FastAPI) -> None:
    # 个人号已加入的群和频道，供设置页点选。没有用户号时 online 为 false。
    @app.get("/api/chats")
    async def list_chats() -> dict:
        provider = app.state.chats_provider
        if provider is None:
            return {"items": [], "online": False, "reason": "会话池未就绪"}
        result = await provider()
        if isinstance(result, dict):
            return result
        return {"items": result, "online": bool(result), "reason": ""}

    # 手动强制从 Telegram 全量校准本地群组/频道缓存。
    @app.post("/api/chats/sync", dependencies=[Depends(require_token)])
    async def sync_chats() -> dict:
        """调用注入的同步函数，供前端在缓存异常或需要立即刷新时使用。"""
        provider = app.state.chats_sync
        if provider is None:
            raise HTTPException(status_code=503, detail="群组缓存服务未就绪")
        result = await provider()
        if isinstance(result, dict):
            return result
        return {"items": result, "online": bool(result), "reason": ""}

    # 用 chat_id 向 Telegram 要官方标题。选群列表改走 GET /api/chats 后，这条只作补查。
    @app.post("/api/chats/resolve")
    async def resolve_chat(payload: ChatIdBody) -> dict:
        resolver = app.state.chat_resolver
        title = ""
        if resolver is not None:
            try:
                title = await resolver(int(payload.chat_id))
            except Exception as error:
                raise HTTPException(status_code=400, detail=f"找不到该群或频道: {error}") from error
        return {"id": int(payload.chat_id), "title": title or ""}

    # 先读本地话题名单。refresh=1 或本地没有记录时，才用个人号问 Telegram。
    @app.get("/api/chats/{chat_id}/topics", dependencies=[Depends(require_token)])
    async def list_chat_topics(chat_id: int, refresh: int = Query(default=0)) -> dict:
        admin = app.state.topic_admin
        if admin is None:
            raise HTTPException(status_code=503, detail="话题管理未就绪")
        try:
            return await admin.list_topics(chat_id, refresh=bool(refresh))
        except TopicAdminError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/chats/{chat_id}/topics", dependencies=[Depends(require_token)])
    async def create_chat_topic(chat_id: int, payload: TopicTitleBody) -> dict:
        admin = app.state.topic_admin
        if admin is None:
            raise HTTPException(status_code=503, detail="话题管理未就绪")
        try:
            created = await admin.create_topic(chat_id, payload.title)
        except TopicAdminError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"ok": True, **created}

    @app.patch("/api/chats/{chat_id}/topics/{topic_id}", dependencies=[Depends(require_token)])
    async def rename_chat_topic(chat_id: int, topic_id: int, payload: TopicTitleBody) -> dict:
        admin = app.state.topic_admin
        if admin is None:
            raise HTTPException(status_code=503, detail="话题管理未就绪")
        try:
            await admin.rename_topic(chat_id, topic_id, payload.title)
        except TopicAdminError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"ok": True}

    @app.delete("/api/chats/{chat_id}/topics/{topic_id}", dependencies=[Depends(require_token)])
    async def delete_chat_topic(chat_id: int, topic_id: int) -> dict:
        admin = app.state.topic_admin
        if admin is None:
            raise HTTPException(status_code=503, detail="话题管理未就绪")
        try:
            await admin.delete_topic(chat_id, topic_id)
        except TopicAdminError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"ok": True}
