"""Telegram 发送实现。

业务层只看 SendOk / SendRetryLater / SendFailed，不要直接 catch Telethon 异常。
FloodWait 是账号节奏信号：必须等待，不能当成文件失败去累加 retry_count。
论坛群发送必须带 reply_to=topic_id，否则消息进 General 而不是创建好的话题。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from telethon.errors import FloodWaitError

from ..domain.task import Task
from ..logger import get_logger
from ..ports.transport import SendFailed, SendOk, SendResult, SendRetryLater

logger = get_logger(__name__)


class TelegramTransport:
    def __init__(self, telegram_client):
        self.telegram_client = telegram_client

    async def send(self, task: Task, timeout_seconds: int) -> SendResult:
        """有封面则视频+图作为相册；相册时取第一条消息 id 当作视频消息。"""
        video_path = task.artifacts.video_path
        if not video_path:
            return SendFailed("缺少视频路径")
        if not Path(video_path).exists():
            return SendFailed(f"文件不存在: {video_path}")

        files = [video_path]
        page_path = task.artifacts.page_path
        if page_path and Path(page_path).exists():
            files.append(page_path)

        send_kwargs: dict = {
            "entity": task.destination.chat_id,
            "file": files if len(files) > 1 else files[0],
            "caption": task.caption or "",
        }
        if len(files) > 1:
            send_kwargs["album"] = True
        # 论坛群不带 reply_to 会进 General，进不了创建好的话题
        if task.destination.topic_id is not None:
            send_kwargs["reply_to"] = task.destination.topic_id

        try:
            sent_messages = await asyncio.wait_for(
                self.telegram_client.send_file(**send_kwargs),
                timeout=timeout_seconds,
            )
        except FloodWaitError as flood_error:
            # 账号被限流，不是这个文件坏了
            return SendRetryLater(flood_error.seconds)
        except TimeoutError:
            return SendFailed(f"上传超时 {timeout_seconds}s")
        except Exception as error:
            logger.exception("Telegram 发送失败: %s", video_path)
            return SendFailed(str(error))

        video_message = sent_messages[0] if isinstance(sent_messages, list) else sent_messages
        return SendOk(video_message.id)
