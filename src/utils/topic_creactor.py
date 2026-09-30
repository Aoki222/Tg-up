"""按「群 + 文件所在目录的绝对路径」复用或创建论坛话题。

同一目录只应有一个 topic_id，按 (chat_id, 目录) 加锁，避免并发创建出两个同名话题。
不同目录可以并行创建。库里已有记录时不占锁。
client 通过 get_client() 现取，这样 session 被热卸载后不会拿着死连接。
发送时还必须把这个 topic_id 传给 Transport 的 reply_to，否则消息进 General。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from telethon.tl.functions.messages import CreateForumTopicRequest

from ..adapters.task_store import TaskRepository
from ..logger import get_logger

logger = get_logger(__name__)

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

                result = await telegram_client(
                    CreateForumTopicRequest(
                        peer=chat_id,
                        title=folder_name,
                    )
                )
                topic_id = self._extract_topic_id(result)
                await self.task_repository.save_chat_topic(chat_id, topic_id, topic_path)
                logger.info("已创建群组话题 chat_id=%s topic_id=%s path=%s", chat_id, topic_id, topic_path)
                return topic_id
        finally:
            await self._release_lock(chat_id, topic_path)

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

    @staticmethod
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
