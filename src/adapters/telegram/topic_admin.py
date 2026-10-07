"""话题管理的编排。个人号调用 Telegram，名单写入 telegram_topics。

路由函数只调用这里。自动按文件夹建话题不经过这个类。
"""

from __future__ import annotations

from ...domain.route_topic import routes_fixed_on_topic
from ...logger import get_logger
from ..db.topics import deactivate_topic, list_topics, replace_topics, upsert_topic
from .topics import (
    TopicAdminError,
    create_forum_topic,
    delete_forum_topic,
    forum_topics,
    rename_forum_topic,
)

logger = get_logger(__name__)


class TopicAdmin:
    def __init__(self, user_client, settings_hub) -> None:
        # user_client() -> (account_name, client, reason)
        self._user_client = user_client
        self._settings = settings_hub

    async def list_topics(self, chat_id: int, *, refresh: bool) -> dict:
        cached = await list_topics(chat_id)
        if cached and not refresh:
            return {"forum": True, "topics": cached, "cached": True, "online": True, "reason": ""}
        account_name, client, reason = await self._user_client()
        if client is None:
            if cached:
                return {"forum": True, "topics": cached, "cached": True, "online": False, "reason": reason}
            return {"forum": False, "topics": [], "cached": False, "online": False, "reason": reason}
        try:
            fresh = await forum_topics(client, chat_id)
        except Exception as error:
            logger.warning("读取话题失败 chat=%s: %s", chat_id, error)
            if cached:
                return {"forum": True, "topics": cached, "cached": True, "online": True, "reason": str(error)}
            raise TopicAdminError("这个个人号不能管理该群的话题") from error
        if fresh is None:
            return {"forum": False, "topics": [], "cached": False, "online": True, "reason": ""}
        await replace_topics(account_name or "", chat_id, fresh)
        return {
            "forum": True,
            "topics": await list_topics(chat_id),
            "cached": False,
            "online": True,
            "reason": "",
        }

    async def create_topic(self, chat_id: int, title: str) -> dict:
        account_name, client, reason = await self._require_client()
        if not title.strip():
            raise TopicAdminError("话题名称不能为空")
        created = await create_forum_topic(client, chat_id, title.strip()[:128])
        await upsert_topic(account_name, chat_id, int(created["topic_id"]), created["title"])
        return created

    async def rename_topic(self, chat_id: int, topic_id: int, title: str) -> None:
        account_name, client, _reason = await self._require_client()
        if not title.strip():
            raise TopicAdminError("话题名称不能为空")
        cleaned = title.strip()[:128]
        await rename_forum_topic(client, chat_id, topic_id, cleaned)
        await upsert_topic(account_name, chat_id, topic_id, cleaned)

    async def delete_topic(self, chat_id: int, topic_id: int) -> None:
        settings = self._settings.get() if self._settings is not None else None
        if settings is not None:
            used = routes_fixed_on_topic(settings, chat_id, topic_id)
            if used:
                raise TopicAdminError("仍有路径使用这个话题，请先改路径")
        _account_name, client, _reason = await self._require_client()
        await delete_forum_topic(client, chat_id, topic_id)
        await deactivate_topic(chat_id, topic_id)

    async def _require_client(self):
        account_name, client, reason = await self._user_client()
        if client is None:
            raise TopicAdminError(reason or "没有可用的个人账号")
        return account_name or "", client, reason
