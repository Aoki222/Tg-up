from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import ForumTopic, ForumTopicDeleted

from src.adapters.telegram.topics import forum_topics


def _make_msg(msg_id: int, date: datetime):
    msg = MagicMock()
    msg.id = msg_id
    msg.date = date
    return msg


def _make_topic(topic_id: int, title: str, top_message: int = 10, date: datetime | None = None):
    topic = MagicMock(spec=ForumTopic)
    topic.id = topic_id
    topic.title = title
    topic.top_message = top_message
    topic.date = date or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return topic


async def test_forum_topics_not_a_forum() -> None:
    client = MagicMock()
    entity = MagicMock()
    entity.forum = False
    client.get_entity = AsyncMock(return_value=entity)

    result = await forum_topics(client, -100123)
    assert result is None


async def test_forum_topics_single_page_filters_deleted_and_handles_general() -> None:
    client = MagicMock()
    entity = MagicMock()
    entity.forum = True
    client.get_entity = AsyncMock(return_value=entity)

    # 包含：
    # 1. 默认话题 id=1，标题为空 -> 应兜底为 General
    # 2. 正常话题 id=2, title="Anime"
    # 3. 已删除话题 ForumTopicDeleted(id=3) -> 应过滤
    deleted_topic = MagicMock(spec=ForumTopicDeleted)
    deleted_topic.id = 3

    page_result = MagicMock()
    page_result.count = 2
    page_result.topics = [
        _make_topic(1, ""),
        _make_topic(2, "Anime"),
        deleted_topic,
    ]
    page_result.messages = []

    client.side_effect = AsyncMock(return_value=page_result)

    result = await forum_topics(client, -100123)
    assert result is not None
    assert len(result) == 2
    assert result[0] == {"topic_id": 1, "title": "General"}
    assert result[1] == {"topic_id": 2, "title": "Anime"}


async def test_forum_topics_multi_page_pagination() -> None:
    client = MagicMock()
    entity = MagicMock()
    entity.forum = True
    client.get_entity = AsyncMock(return_value=entity)

    d1 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    d2 = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    # 第一页满 100 个话题
    page1_topics = [_make_topic(i, f"Topic {i}", top_message=i * 10, date=d1) for i in range(1, 101)]
    page1_messages = [_make_msg(1000, d1)]  # 最后一个话题 top_message 是 1000
    page1_topics[-1].top_message = 1000

    page1_result = MagicMock()
    page1_result.count = 120
    page1_result.topics = page1_topics
    page1_result.messages = page1_messages

    # 第二页 20 个话题（不满 100，最后一页）
    page2_topics = [_make_topic(i, f"Topic {i}", top_message=i * 10, date=d2) for i in range(101, 121)]
    page2_result = MagicMock()
    page2_result.count = 120
    page2_result.topics = page2_topics
    page2_result.messages = []

    calls = []

    async def mock_call(req):
        if isinstance(req, GetForumTopicsRequest):
            calls.append(req)
            if len(calls) == 1:
                return page1_result
            return page2_result
        return None

    client.side_effect = mock_call

    result = await forum_topics(client, -100123)
    assert result is not None
    assert len(result) == 120
    assert len(calls) == 2

    # 验证第二次翻页的游标
    req2 = calls[1]
    assert req2.offset_topic == 100
    assert req2.offset_id == 1000
    assert req2.offset_date == d1


async def test_forum_topics_stops_when_cursor_stuck() -> None:
    client = MagicMock()
    entity = MagicMock()
    entity.forum = True
    client.get_entity = AsyncMock(return_value=entity)

    # 模拟极端情况：服务端连续两次返回完全相同的一组满 100 数据
    t1 = _make_topic(10, "Same", top_message=50)
    page_result = MagicMock()
    page_result.count = 500
    page_result.topics = [t1] * 100
    page_result.messages = []

    client.side_effect = AsyncMock(return_value=page_result)

    result = await forum_topics(client, -100123)
    assert result is not None
    # 去重后只有 1 个
    assert len(result) == 1
    # 并且不会陷入无限死循环
