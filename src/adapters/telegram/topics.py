"""论坛话题的列出、新建、改名和删除。

只接收已经连上的客户端，不写数据库。
自动按文件夹建话题不走这里，仍由 TopicCreator 使用先连上的账号。
"""

from __future__ import annotations

from telethon import TelegramClient
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    DeleteTopicHistoryRequest,
    EditForumTopicRequest,
    GetForumTopicsRequest,
)

from ...logger import get_logger

logger = get_logger(__name__)


class TopicAdminError(RuntimeError):
    """个人号管不了这个群的话题，或这个群不是论坛。"""


async def forum_topics(client: TelegramClient, chat_id: int) -> list[dict] | None:
    """返回话题列表。不是论坛群时返回 None。"""
    entity = await client.get_entity(chat_id)
    if not getattr(entity, "forum", False):
        return None
    result = await client(
        GetForumTopicsRequest(
            peer=chat_id,
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=100,
        )
    )
    items: list[dict] = []
    for topic in getattr(result, "topics", []) or []:
        topic_id = int(getattr(topic, "id", 0) or 0)
        if topic_id <= 0:
            continue
        items.append(
            {
                "topic_id": topic_id,
                "title": str(getattr(topic, "title", "") or ""),
            }
        )
    return items


async def create_forum_topic(client: TelegramClient, chat_id: int, title: str) -> dict:
    result = await client(CreateForumTopicRequest(peer=chat_id, title=title))
    topic_id = _topic_id_from_updates(result)
    logger.info("已新建话题 chat=%s topic=%s title=%s", chat_id, topic_id, title)
    return {"topic_id": topic_id, "title": title}


async def rename_forum_topic(client: TelegramClient, chat_id: int, topic_id: int, title: str) -> None:
    await client(EditForumTopicRequest(peer=chat_id, topic_id=topic_id, title=title))


async def delete_forum_topic(client: TelegramClient, chat_id: int, topic_id: int) -> None:
    """删除话题及其消息。top_msg_id 就是话题编号。"""
    await client(DeleteTopicHistoryRequest(peer=chat_id, top_msg_id=topic_id))


def _topic_id_from_updates(result) -> int:
    updates = getattr(result, "updates", None) or []
    for update in updates:
        message = getattr(update, "message", None)
        if message is not None and getattr(message, "id", None):
            return int(message.id)
    raise TopicAdminError("Telegram 没有返回话题编号")
