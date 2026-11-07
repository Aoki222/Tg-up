"""按「群 + 文件所在目录的绝对路径」复用或创建论坛话题。

同一目录只应有一个 topic_id，按 (chat_id, 目录) 加锁，避免并发创建出两个同名话题。
不同目录可以并行创建。库里已有记录时不占锁。
映射没有这条路径时，先按文件夹名匹配这个群里已有的话题，对上就复用。
get_client() 取当前先连上的账号。管理员机器人也可以创建话题。
session 被热卸载后不会拿着死连接，因为每次现取。
发送时还必须把这个 topic_id 传给 Transport 的 reply_to，否则消息进 General。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from pathlib import Path

from telethon.tl.functions.messages import CreateForumTopicRequest

from ..adapters.db.topics import list_topics, replace_topics, upsert_topic
from ..adapters.task_store import TaskRepository
from ..adapters.telegram.topics import forum_topics
from ..database.connection import pool_is_open
from ..logger import get_logger

logger = get_logger(__name__)

# Telegram 支持的 6 种话题颜色（来自官方颜色选择器，传其他值会报错）
_TOPIC_COLORS = [0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F]

# 启动时入库和连接 session 同时开始。磁盘上已有 *.session 也不代表客户端已经进池。
_CLIENT_WAIT_SECONDS = 20.0
_CLIENT_POLL_SECONDS = 0.5


class TopicSessionUnavailable(RuntimeError):
    """进程里还没有连上的 Telegram 客户端，话题先不建。"""


class TopicCreator:
    """按文件目录为群组复用或创建 Telegram 话题。"""

    def __init__(
        self,
        get_client: Callable,
        task_repository: TaskRepository,
        client_wait_seconds: float = _CLIENT_WAIT_SECONDS,
    ):
        # get_client 每次现取，避免绑死某个后来被卸掉的 session
        self.get_client = get_client
        self.task_repository = task_repository
        self._client_wait_seconds = client_wait_seconds
        self._folder_locks: dict[tuple[int, str], asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._client_wait: asyncio.Task | None = None
        self._client_wait_guard = asyncio.Lock()

    async def _lock_for(self, chat_id: int, topic_path: str) -> asyncio.Lock:
        key = (chat_id, topic_path)
        async with self._locks_guard:
            lock = self._folder_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._folder_locks[key] = lock
            return lock

    async def _release_lock(self, chat_id: int, topic_path: str) -> None:
        """锁无等待者时从字典中移除，避免内存泄漏。"""
        key = (chat_id, topic_path)
        async with self._locks_guard:
            lock = self._folder_locks.get(key)
            if lock is not None and not lock.locked():
                del self._folder_locks[key]

    async def get_or_create_topic(self, fold_path: str, folder_name: str, chat_id: int) -> int:
        """按群组和目录路径复用话题，目录变化时创建新话题。"""
        topic_path = str(Path(fold_path).resolve())
        existing_topic_id = await self.task_repository.get_chat_topic(chat_id, topic_path)
        if existing_topic_id is not None:
            return existing_topic_id

        telegram_client = await self._await_client()
        if telegram_client is None:
            raise TopicSessionUnavailable("没有可用的 Telegram session，无法创建话题")

        lock = await self._lock_for(chat_id, topic_path)
        try:
            async with lock:
                existing_topic_id = await self.task_repository.get_chat_topic(chat_id, topic_path)
                if existing_topic_id is not None:
                    return existing_topic_id

                telegram_client = self.get_client()
                if telegram_client is None:
                    raise TopicSessionUnavailable("没有可用的 Telegram session，无法创建话题")

                matched = await self._match_existing(telegram_client, chat_id, folder_name)
                if matched is not None:
                    await self.task_repository.save_chat_topic(chat_id, matched, topic_path)
                    logger.info(
                        "复用群里同名话题 chat_id=%s topic_id=%s name=%s path=%s",
                        chat_id,
                        matched,
                        folder_name.strip(),
                        topic_path,
                    )
                    return matched

                result = await telegram_client(
                    CreateForumTopicRequest(
                        peer=chat_id,
                        title=folder_name,
                        icon_color=random.choice(_TOPIC_COLORS),
                    )
                )
                topic_id = _extract_topic_id(result)
                await self.task_repository.save_chat_topic(chat_id, topic_id, topic_path)
                # 正式进程里连接池已打开，顺手写入话题名单。单测没有库，不能在这里打开池。
                if pool_is_open():
                    try:
                        await upsert_topic("", chat_id, topic_id, folder_name)
                    except Exception:
                        logger.debug("话题名单未写入 chat=%s topic=%s", chat_id, topic_id, exc_info=True)
                logger.info("已创建群组话题 chat_id=%s topic_id=%s path=%s", chat_id, topic_id, topic_path)
                return topic_id
        finally:
            await self._release_lock(chat_id, topic_path)

    async def _match_existing(self, client, chat_id: int, folder_name: str) -> int | None:
        """按标题找这个群里已有的话题。本地没有就向 Telegram 拉一次。拉失败则返回 None，由调用方新建。"""
        wanted = folder_name.strip()
        if not wanted or client is None:
            return None
        cached: list[dict] = []
        if pool_is_open():
            try:
                cached = await list_topics(chat_id)
            except Exception:
                logger.debug("读取本地话题名单失败 chat=%s", chat_id, exc_info=True)
                cached = []
        chosen = _pick_same_title(wanted, cached)
        if chosen is not None:
            _log_duplicate_titles(wanted, chat_id, cached, chosen)
            return chosen
        try:
            fresh = await forum_topics(client, chat_id)
        except Exception:
            logger.warning(
                "读取群话题失败，将新建同名话题 chat=%s name=%s",
                chat_id,
                wanted,
                exc_info=True,
            )
            return None
        if not fresh:
            return None
        if pool_is_open():
            try:
                await replace_topics("", chat_id, fresh)
            except Exception:
                logger.debug("话题名单未刷新 chat=%s", chat_id, exc_info=True)
        chosen = _pick_same_title(wanted, fresh)
        if chosen is not None:
            _log_duplicate_titles(wanted, chat_id, fresh, chosen)
        return chosen

    async def _await_client(self):
        """同一轮等待只轮询一次。多个目录同时建话题时共用这次结果。"""
        async with self._client_wait_guard:
            task = self._client_wait
            if task is None or task.done():
                task = asyncio.create_task(self._poll_client())
                self._client_wait = task
        return await task

    async def _poll_client(self):
        deadline = time.monotonic() + self._client_wait_seconds
        announced = False
        while True:
            client = self.get_client()
            if client is not None:
                return client
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if not announced:
                logger.info("等待 Telegram session 连上后再创建话题")
                announced = True
            await asyncio.sleep(min(_CLIENT_POLL_SECONDS, remaining))

def _pick_same_title(wanted: str, topics: list[dict]) -> int | None:
    matches = sorted(
        int(item["topic_id"])
        for item in topics
        if str(item.get("title") or "").strip() == wanted and int(item.get("topic_id") or 0) > 0
    )
    if not matches:
        return None
    return matches[0]


def _log_duplicate_titles(wanted: str, chat_id: int, topics: list[dict], chosen: int) -> None:
    matches = [
        int(item["topic_id"])
        for item in topics
        if str(item.get("title") or "").strip() == wanted
    ]
    if len(matches) <= 1:
        return
    logger.warning(
        "群里有多个同名话题，使用较早的一个 chat_id=%s name=%s topic_id=%s candidates=%s",
        chat_id,
        wanted,
        chosen,
        sorted(matches),
    )


def _extract_topic_id(result) -> int:
    """从 CreateForumTopicRequest 的 Updates 里取出话题根消息 id。"""
    for update in getattr(result, "updates", ()):
        message = getattr(update, "message", None)
        message_id = getattr(message, "id", None)
        if message_id is not None:
            return int(message_id)
        update_id = getattr(update, "id", None)
        if update_id is not None:
            return int(update_id)
    raise RuntimeError("Telegram 创建话题成功但未找到 topic_id")
