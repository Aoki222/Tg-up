"""话题管理：为 model_name 在 TG 群组创建同名话题并映射入库。

职责：
- 从 download/<model_name>/*.mp4 提取 model_name（即文件所在的一级子目录名）
- 在目标群组用 CreateForumTopicRequest 创建名为 model_name 的话题
- 把 model_name -> topic_id 写入 SQLite（数据库层见 database/）

解耦说明：本文件只提供「取 model_name + 创建话题」的函数/类，
供上层编排调用；存储逻辑全部委托给 database.TopicRepo。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.messages import CreateForumTopicRequest

from ..config import config
from ..logger import get_logger

logger = get_logger(__name__)


def extract_model_name(file_path: str | Path, watch_root: str | Path) -> Optional[str]:
    """从 download/<model_name>/*.mp4 中提取 model_name。

    规则：文件必须位于监控根目录的一级子目录内，
    一级子目录名即 model_name。不在该层级则返回 None。
    """
    path = Path(file_path).resolve()
    root = Path(watch_root).resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    # download/<model_name>/<file>：父目录必须恰好是根目录的一级子目录
    if len(rel.parts) >= 2:
        return rel.parts[0]
    return None


class TopicService:
    """在目标 TG 群组创建话题，并把 model_name -> topic_id 写入数据库。"""

    def __init__(
        self,
        client: TelegramClient,
        target_chat_id: int,
        max_retries: int = 3,
        max_flood_wait: int = 300,
    ) -> None:
        self.client = client
        self.target_chat_id = target_chat_id
        self.max_retries = max_retries
        self.max_flood_wait = max_flood_wait

    async def get_or_create(self, model_name: str) -> Optional[int]:
        """返回 model_name 对应的 topic_id：先查库，无则创建并入库。"""
        # 1. 本地已有映射则直接返回
        existing = await self.repo.get_topic_id(model_name)
        if existing is not None:
            return existing

        # 2. 创建话题
        topic_id = await self._create_topic(model_name)
        if topic_id is not None:
            await self.repo.set_topic_id(model_name, topic_id)
            logger.info("已创建并映射话题 '%s' -> topic_id=%s", model_name, topic_id)
        return topic_id

    async def _create_topic(self, title: str) -> Optional[int]:
        """调用 CreateForumTopicRequest 创建话题并解析出 topic_id。"""
        for attempt in range(self.max_retries):
            try:
                result = await self.client(
                    CreateForumTopicRequest(
                        peer=self.target_chat_id,
                        title=title,
                        icon_color=0x6FB9F0,
                    )
                )
                topic_id = self._extract_topic_id(result)
                if topic_id is None:
                    logger.error("无法从创建结果中解析 topic_id: %s", result)
                    return None
                return topic_id

            except FloodWaitError as e:
                if e.seconds > self.max_flood_wait or attempt >= self.max_retries - 1:
                    logger.error("创建话题 '%s' 时 FloodWait 过久/重试耗尽: %ss", title, e.seconds)
                    return None
                logger.warning("创建话题 '%s' 触发 FloodWait，等待 %ss", title, e.seconds)
                await asyncio.sleep(e.seconds)

            except RPCError as e:
                msg = str(e).lower()
                if "topic" in msg and ("exist" in msg or "duplicate" in msg):
                    # 竞态：已存在但本地无映射，无法用 bot 查询，交由上层处理
                    logger.warning("话题 '%s' 已存在（竞态）: %s", title, e)
                else:
                    logger.error("创建话题 '%s' 失败: %s", title, e)
                return None

            except Exception as e:  # noqa: BLE001
                logger.error("创建话题 '%s' 出现意外错误: %s", title, e)
                return None
        return None

    @staticmethod
    def _extract_topic_id(result) -> Optional[int]:
        """从 CreateForumTopicRequest 返回的 Updates 中提取 message_thread_id。"""
        updates = getattr(result, "updates", None)
        if not updates:
            return None
        for update in updates:
            message = getattr(update, "message", None)
            if message is not None and getattr(message, "id", None) is not None:
                return message.id
        return None


def chat_id_from_config() -> int:
    """从 config 读取目标群组 ID 并转为 int。"""
    if config.target_chat_id is None:
        raise ValueError("未在 .env 中配置 TARGET_CHAT_ID")
    return int(config.target_chat_id)
