"""Telegram 发送实现。

业务层只看 SendOk / SendRetryLater / SendFailed，不要直接 catch Telethon 异常。
FloodWait 是账号节奏信号：必须等待，不能当成文件失败去累加 retry_count。
论坛群发送必须带 reply_to=topic_id，否则消息进 General 而不是创建好的话题。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from telethon.errors import FloodWaitError

from ..domain.task import Task
from ..logger import get_logger
from ..ports.transport import SendDisconnected, SendFailed, SendOk, SendResult, SendRetryLater
from ..utils.FastTelethon import upload_file as fast_upload_file

logger = get_logger(__name__)

FAST_UPLOAD_WORKERS = 6
FAST_UPLOAD_ATTEMPTS = 2


class _FastUploadError(Exception):
    """FastTelethon 分块阶段失败，可安全重建句柄并重试。"""


class _FastMessageError(Exception):
    """FastTelethon 句柄已经提交到 Telegram 后的消息阶段失败。"""


class TelegramTransport:
    """一个 session 一个实例，不要多个 Worker 共用同一个 client。"""
    def __init__(self, telegram_client):
        self.telegram_client = telegram_client

    async def send(
        self,
        task: Task,
        timeout_seconds: int,
        on_progress: Callable[[float, float], None] | None = None,
    ) -> SendResult:
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

        if not self.telegram_client.is_connected():
            return SendDisconnected("Telegram client 未连接")

        for attempt in range(1, FAST_UPLOAD_ATTEMPTS + 1):
            try:
                return await asyncio.wait_for(
                    self._send_fast(task, files, on_progress),
                    timeout=timeout_seconds,
                )
            except _FastUploadError as error:
                logger.warning(
                    "FastTelethon 分块上传失败，第 %s/%s 次: %s",
                    attempt,
                    FAST_UPLOAD_ATTEMPTS,
                    error,
                )
                if attempt < FAST_UPLOAD_ATTEMPTS:
                    continue
                logger.warning("FastTelethon 连续失败，回退原生 send_file: %s", video_path)
                return await self._send_native(task, files, timeout_seconds, on_progress)
            except _FastMessageError as error:
                if len(files) > 1:
                    logger.warning("FastTelethon 视频优先补偿也失败，回退原生整组发送")
                    return await self._send_native(task, files, timeout_seconds, on_progress)
                return _classify_send_error(error.__cause__ or error, "FastTelethon 消息提交失败")
            except FloodWaitError as flood_error:
                return SendRetryLater(flood_error.seconds)
            except TimeoutError:
                return SendFailed("FastTelethon 上传超时，未自动重发以避免重复消息")
            except Exception as error:
                if _is_disconnect_error(error):
                    return SendDisconnected(str(error))
                logger.exception("FastTelethon 发送失败: %s", video_path)
                return SendFailed(str(error))

        return SendFailed("FastTelethon 上传失败")

    async def _send_fast(
        self,
        task: Task,
        files: list[str],
        on_progress: Callable[[float, float], None] | None,
    ) -> SendResult:
        """先上传所有媒体句柄，再用同一次 send_file 提交单文件或相册。"""
        total_size = sum(Path(path).stat().st_size for path in files)
        completed_size = 0
        handles = []
        for path in files:
            base = completed_size

            def report(current: int, _total: int, base: int = base) -> None:
                if on_progress is not None:
                    on_progress(base + current, total_size)

            try:
                handle = await fast_upload_file(
                    self.telegram_client,
                    path,
                    progress_callback=report,
                    max_workers=FAST_UPLOAD_WORKERS,
                )
            except FloodWaitError:
                raise
            except Exception as error:
                raise _FastUploadError(str(error)) from error
            handles.append(handle)
            completed_size += Path(path).stat().st_size

        send_kwargs = self._send_kwargs(task, handles if len(handles) > 1 else handles[0])
        if len(handles) > 1:
            send_kwargs["album"] = True
        send_kwargs["force_document"] = False
        send_kwargs["supports_streaming"] = True
        try:
            sent_messages = await self.telegram_client.send_file(**send_kwargs)
        except FloodWaitError:
            raise
        except Exception as error:
            if len(handles) == 1:
                raise _FastMessageError(str(error)) from error
            if _is_disconnect_error(error):
                raise
            return await self._send_album_failure_fallback(task, handles, error)

        video_message = sent_messages[0] if isinstance(sent_messages, list) else sent_messages
        return SendOk(video_message.id)

    async def _send_album_failure_fallback(self, task: Task, handles: list, album_error: Exception) -> SendResult:
        """相册失败后优先保住视频，再尝试用两个句柄重新提交相册。"""
        logger.warning("FastTelethon 相册提交失败，先单独发送视频: %s", album_error)
        video_kwargs = self._send_kwargs(task, handles[0])
        video_kwargs["force_document"] = False
        video_kwargs["supports_streaming"] = True
        try:
            video_message = await self.telegram_client.send_file(**video_kwargs)
        except FloodWaitError:
            raise
        except Exception as video_error:
            if _is_disconnect_error(video_error):
                raise
            logger.warning("FastTelethon 单独发送视频失败，重新尝试整组句柄: %s", video_error)
            album_kwargs = self._send_kwargs(task, handles)
            album_kwargs["album"] = True
            album_kwargs["force_document"] = False
            album_kwargs["supports_streaming"] = True
            try:
                messages = await self.telegram_client.send_file(**album_kwargs)
            except FloodWaitError:
                raise
            except Exception as retry_error:
                raise _FastMessageError(str(retry_error)) from retry_error
            first = messages[0] if isinstance(messages, list) else messages
            return SendOk(first.id)

        logger.warning("视频已发送，封面未随相册发送，按方案 B 结束本任务: %s", task.file_path)
        return SendOk(video_message.id)

    async def _send_native(
        self,
        task: Task,
        files: list[str],
        timeout_seconds: int,
        on_progress: Callable[[float, float], None] | None,
    ) -> SendResult:
        """FastTelethon 分块失败后的原生整组回退路径。"""
        send_kwargs = self._send_kwargs(task, files if len(files) > 1 else files[0])
        if len(files) > 1:
            send_kwargs["album"] = True
        if on_progress is not None:
            send_kwargs["progress_callback"] = on_progress
        try:
            sent_messages = await asyncio.wait_for(
                self.telegram_client.send_file(**send_kwargs),
                timeout=timeout_seconds,
            )
        except FloodWaitError as flood_error:
            return SendRetryLater(flood_error.seconds)
        except TimeoutError:
            return SendFailed(f"上传超时 {timeout_seconds}s")
        except Exception as error:
            if _is_disconnect_error(error):
                return SendDisconnected(str(error))
            logger.exception("Telegram 原生发送失败: %s", files[0])
            return SendFailed(str(error))

        video_message = sent_messages[0] if isinstance(sent_messages, list) else sent_messages
        return SendOk(video_message.id)

    @staticmethod
    def _send_kwargs(task: Task, file) -> dict:
        """构建 Fast 和原生路径共用的目标、caption、话题参数。"""
        send_kwargs: dict = {
            "entity": task.destination.chat_id,
            "file": file,
            "caption": task.caption or "",
        }
        if task.destination.topic_id is not None:
            send_kwargs["reply_to"] = task.destination.topic_id
        return send_kwargs


def _is_disconnect_error(error: BaseException) -> bool:
    if isinstance(error, (ConnectionError, OSError, BrokenPipeError, ConnectionResetError)):
        return True
    text = str(error).lower()
    needles = (
        "disconnect",
        "not connected",
        "connection reset",
        "connection closed",
        "server closed",
        "network is unreachable",
        "timed out",
        "timeout",
        "name or service not known",
        "temporary failure",
    )
    return any(item in text for item in needles)


def _classify_send_error(error: BaseException, prefix: str) -> SendResult:
    """将消息提交阶段异常转换为 Transport 统一结果，避免重复提交。"""
    if isinstance(error, FloodWaitError):
        return SendRetryLater(error.seconds)
    if isinstance(error, TimeoutError):
        return SendFailed(f"{prefix}: 请求超时，未自动重发")
    if _is_disconnect_error(error):
        return SendDisconnected(str(error))
    logger.exception("%s: %s", prefix, error)
    return SendFailed(str(error))
