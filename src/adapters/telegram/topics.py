"""论坛话题的列出、新建、改名和删除。

只接收已经连上的客户端，不写数据库。
自动按文件夹建话题不走这里，仍由 TopicCreator 使用先连上的账号。
"""

from __future__ import annotations

from datetime import datetime

from telethon import TelegramClient
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    DeleteTopicHistoryRequest,
    EditForumTopicRequest,
    GetForumTopicsRequest,
)
from telethon.tl.types import ForumTopicDeleted

from ...logger import get_logger

logger = get_logger(__name__)

# 安全熔断：最多翻页次数和话题总数上限
_MAX_PAGES = 50
_MAX_TOPICS = 5000
_PAGE_LIMIT = 100


class TopicAdminError(RuntimeError):
    """个人号管不了这个群的话题，或这个群不是论坛。"""


async def forum_topics(client: TelegramClient, chat_id: int) -> list[dict] | None:
    """返回该论坛群的全量话题列表（自动分页）。不是论坛群时返回 None。"""
    entity = await client.get_entity(chat_id)
    if not getattr(entity, "forum", False):
        return None

    seen_ids: set[int] = set()
    items: list[dict] = []

    # 游标状态
    offset_date: datetime | None = None
    offset_id: int = 0
    offset_topic: int = 0

    for page in range(_MAX_PAGES):
        result = await client(
            GetForumTopicsRequest(
                peer=chat_id,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=_PAGE_LIMIT,
            )
        )

        raw_topics = getattr(result, "topics", []) or []
        if not raw_topics:
            break

        # 构建当前页消息 ID -> 日期的映射表
        messages = getattr(result, "messages", []) or []
        msg_date_map = {
            int(getattr(m, "id", 0) or 0): getattr(m, "date", None)
            for m in messages
            if getattr(m, "id", None)
        }

        page_new = 0
        for topic in raw_topics:
            # 过滤已删除的话题
            if isinstance(topic, ForumTopicDeleted):
                continue

            topic_id = int(getattr(topic, "id", 0) or 0)
            if topic_id <= 0 or topic_id in seen_ids:
                continue
            seen_ids.add(topic_id)

            title = str(getattr(topic, "title", "") or "").strip()
            # 默认话题（id=1）可能没有显式标题，兜底为 General
            if topic_id == 1 and not title:
                title = "General"

            items.append({"topic_id": topic_id, "title": title})
            page_new += 1

        # 没有新有效话题说明游标未推进，退出防死循环
        if page_new == 0:
            break

        # 达到服务端返回的话题总量时，提前退出
        total_count = getattr(result, "count", 0)
        if total_count and len(seen_ids) >= total_count:
            break

        # 达到系统安全上限
        if len(items) >= _MAX_TOPICS:
            logger.warning("话题数量达到上限 %s，停止翻页 chat=%s", _MAX_TOPICS, chat_id)
            break

        # 本页返回的原始话题数量小于页上限，说明已经是最后一页
        if len(raw_topics) < _PAGE_LIMIT:
            break

        # 游标更新：必须使用上一页最后一个话题的真实属性
        last_topic = raw_topics[-1]
        next_offset_topic = int(getattr(last_topic, "id", 0) or 0)
        next_offset_id = int(getattr(last_topic, "top_message", 0) or 0)
        next_offset_date = msg_date_map.get(next_offset_id) or getattr(last_topic, "date", None)

        # 游标完全无推进，退出防死循环
        if next_offset_topic == offset_topic and next_offset_id == offset_id:
            break

        offset_topic = next_offset_topic
        offset_id = next_offset_id
        offset_date = next_offset_date

    logger.info("话题拉取完成 chat=%s 共 %s 个（翻页 %s 次）", chat_id, len(items), page + 1)
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
