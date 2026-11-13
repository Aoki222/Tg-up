"""Telegram 发送实现。

业务层只看 SendOk / SendRetryLater / SendFailed，不要直接 catch Telethon 异常。
FloodWait 是账号节奏信号：必须等待，不能当成文件失败去累加 retry_count。
论坛群发送必须带 reply_to=topic_id，否则消息进 General 而不是创建好的话题。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from telethon.errors import FloodWaitError, MediaInvalidError
from telethon.tl.types import InputFile, InputFileBig

from ..domain import limits
from ..domain.task import Task
from ..logger import get_logger
from ..ports.transport import SendDisconnected, SendFailed, SendOk, SendOversized, SendResult, SendRetryLater
from ..utils.FastTelethon import UploadInterrupted, describe_taskgroup_error, upload_file as fast_upload_file

logger = get_logger(__name__)

FAST_UPLOAD_WORKERS = 8
FAST_UPLOAD_ATTEMPTS = 2

_VIDEO_SUFFIXES = frozenset({
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".webm",
    ".ts", ".mpeg", ".mpg", ".flv",
})


def telegram_upload_name(path: str) -> str:
    """Telegram 相册把 .m4v/.mov 等当普通文档，和封面图组在一起会 MediaInvalid。"""
    original = Path(path)
    suffix = original.suffix.lower()
    if suffix in _VIDEO_SUFFIXES and suffix != ".mp4":
        return f"{original.stem}.mp4"
    return original.name


class _NamedFile:
    """让 Telethon 按自定义文件名猜 mime，读的仍是磁盘上的原文件。

    必须声明 seekable。否则 Telethon 认为长度未知，会 read() 不带上限，
    把整份视频放进内存。
    """

    def __init__(self, path: str, name: str):
        self.name = name
        self._size = os.path.getsize(path)
        self._fh = open(path, "rb")

    def seekable(self) -> bool:
        return True

    def read(self, size: int = -1):
        if size is None or size < 0:
            return self._fh.read()
        return self._fh.read(size)

    def seek(self, offset: int, whence: int = 0):
        return self._fh.seek(offset, whence)

    def tell(self) -> int:
        return self._fh.tell()

    def close(self) -> None:
        self._fh.close()


class _FastUploadError(Exception):
    """FastTelethon 分块阶段失败，可安全重建句柄并重试。"""


class _FastMessageError(Exception):
    """FastTelethon 句柄已经提交到 Telegram 后的消息阶段失败。"""


class TelegramTransport:
    """一个 session 一个实例，不要多个 Worker 共用同一个 client。"""

    def __init__(self, telegram_client, sender_pool=None):
        self.telegram_client = telegram_client
        self.sender_pool = sender_pool
        self._partial: dict[str, UploadInterrupted] = {}

    async def invalidate_pool(self) -> None:
        """主连接断开或网络异常时，将底层连接池中的连接置为脏并清理。"""
        if self.sender_pool is not None and hasattr(self.sender_pool, "invalidate_all"):
            res = self.sender_pool.invalidate_all()
            if asyncio.iscoroutine(res):
                await res

    async def close(self) -> None:
        """Worker 退出时关闭底层连接池。"""
        if self.sender_pool is not None and hasattr(self.sender_pool, "close"):
            await self.sender_pool.close()

    async def send(
        self,
        task: Task,
        timeout_seconds: int,
        on_progress: Callable[[float, float], None] | None = None,
        fast_workers: int | None = None,
    ) -> SendResult:
        """有封面则视频+图作为相册；相册时取第一条消息 id 当作视频消息。"""
        video_path = task.artifacts.video_path
        if not video_path:
            return SendFailed("缺少视频路径")
        if not Path(video_path).exists():
            return SendFailed(f"文件不存在: {video_path}", retryable=False)

        files = [video_path]
        page_path = task.artifacts.page_path
        if page_path and Path(page_path).exists():
            files.append(page_path)

        if not self.telegram_client.is_connected():
            return SendDisconnected("Telegram client 未连接")

        for attempt in range(1, FAST_UPLOAD_ATTEMPTS + 1):
            try:
                result = await asyncio.wait_for(
                    self._send_fast(task, files, on_progress, fast_workers=fast_workers),
                    timeout=timeout_seconds,
                )
                self._partial.pop(video_path, None)
                return result
            except UploadInterrupted as interrupted:
                if _is_parts_invalid(interrupted):
                    self._partial.pop(video_path, None)
                    return SendOversized(limits.PARTS_INVALID_REASON)
                self._partial[video_path] = interrupted
                logger.warning(
                    "FastTelethon 连接断开，已保留 %s/%s 个分片，等重连后继续: %s",
                    len(interrupted.done),
                    interrupted.total_parts,
                    video_path,
                )
                await self.invalidate_pool()
                return SendDisconnected(str(interrupted))
            except _FastUploadError as error:
                if _is_parts_invalid(error):
                    logger.warning("文件分片超过 Telegram 上限，停止重试: %s", error)
                    return SendOversized(limits.PARTS_INVALID_REASON)
                if _is_disconnect_error(error):
                    logger.warning("FastTelethon 连接断开，保持已完成分片，不立刻重传: %s", error)
                    await self.invalidate_pool()
                    return SendDisconnected(str(error))
                if video_path in self._partial:
                    logger.warning(
                        "续传失败，保留已完成分片，不退回原生上传: %s",
                        error,
                    )
                    return SendDisconnected(str(error))
                logger.warning(
                    "FastTelethon 分块上传失败，第 %s/%s 次: %s",
                    attempt,
                    FAST_UPLOAD_ATTEMPTS,
                    error,
                )
                if attempt < FAST_UPLOAD_ATTEMPTS:
                    continue
                logger.warning("FastTelethon 连续失败，回退原生 send_file: %s", video_path)
                self._partial.pop(video_path, None)
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
        fast_workers: int | None = None,
    ) -> SendResult:
        """先上传所有媒体句柄，再用同一次 send_file 提交单文件或相册。"""
        video_path = task.artifacts.video_path or files[0]
        page_path = task.artifacts.page_path
        slots = max(1, int(fast_workers or FAST_UPLOAD_WORKERS))
        # 时长和分辨率与分块上传同时进行，提交前再取结果。
        attributes_task = asyncio.create_task(
            asyncio.to_thread(extract_video_attributes, task.artifacts.video_path)
        )
        total_size = sum(Path(path).stat().st_size for path in files)
        completed_size = 0
        handles = []
        try:
            for path in files:
                base = completed_size

                def report(current: int, _total: int, base: int = base) -> None:
                    if on_progress is not None:
                        on_progress(base + current, total_size)

                saved = self._partial.get(path)
                file_size = Path(path).stat().st_size
                resume_id = None
                resume_parts: set[int] | None = None
                if saved is not None and saved.file_size == file_size and saved.done:
                    resume_id = saved.file_id
                    resume_parts = set(saved.done)
                workers = slots if path == video_path else 1
                try:
                    handle = await fast_upload_file(
                        self.telegram_client,
                        path,
                        progress_callback=report,
                        max_workers=workers,
                        sender_pool=self.sender_pool,
                        file_id=resume_id,
                        skip_parts=resume_parts,
                    )
                except UploadInterrupted:
                    raise
                except FloodWaitError:
                    raise
                except Exception as error:
                    raise _FastUploadError(describe_taskgroup_error(error)) from error
                handles.append(_rename_input_file(handle, path))
                completed_size += Path(path).stat().st_size
        except BaseException:
            if not attributes_task.done():
                attributes_task.cancel()
            raise
        attributes = await attributes_task

        thumb_handle = await self._upload_thumb(
            video_path, page_path, need_preview=task.policy.need_preview
        )
        try:
            return await self._submit_media(
                task, handles, thumb_handle=thumb_handle, attributes=attributes
            )
        except FloodWaitError:
            raise
        except Exception as error:
            if len(handles) == 1:
                raise _FastMessageError(str(error)) from error
            if _is_disconnect_error(error):
                raise
            return await self._send_album_failure_fallback(task, handles, error, thumb_handle=thumb_handle)

    async def _send_album_failure_fallback(self, task: Task, handles: list, album_error: Exception, thumb_handle=None) -> SendResult:
        """相册失败后优先保住视频，再尝试用两个句柄重新提交相册。"""
        logger.warning("相册提交失败，先单独发送视频: %s", album_error)
        try:
            video_message = await self.telegram_client.send_file(
                **self._send_kwargs(task, handles[0], thumb_handle=thumb_handle)
            )
        except FloodWaitError:
            raise
        except Exception as video_error:
            if _is_disconnect_error(video_error):
                raise
            logger.warning("单独发送视频失败，重新尝试整组句柄: %s", video_error)
            try:
                messages = await self.telegram_client.send_file(**self._send_kwargs(task, handles, thumb_handle=thumb_handle))
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
        video_path = task.artifacts.video_path or files[0]
        page_path = task.artifacts.page_path
        thumb_handle = await self._upload_thumb(
            video_path, page_path, need_preview=task.policy.need_preview
        )
        attributes = await asyncio.to_thread(extract_video_attributes, video_path)

        prepared, cleanup = _prepare_native_files(files)
        send_kwargs = self._send_kwargs(
            task,
            prepared if len(prepared) > 1 else prepared[0],
            thumb_handle=thumb_handle,
            attributes=attributes,
        )
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
            if _is_parts_invalid(error):
                return SendOversized(limits.PARTS_INVALID_REASON)
            if len(prepared) > 1 and _is_album_invalid(error):
                logger.warning("原生相册提交失败，改为只发视频: %s", error)
                return await self._send_native_video_only(
                    task, files[0], timeout_seconds, on_progress, thumb_handle=thumb_handle
                )
            logger.exception("Telegram 原生发送失败: %s", files[0])
            return SendFailed(str(error))
        finally:
            _close_files(cleanup)

        video_message = sent_messages[0] if isinstance(sent_messages, list) else sent_messages
        return SendOk(video_message.id)

    async def _send_native_video_only(
        self,
        task: Task,
        video_path: str,
        timeout_seconds: int,
        on_progress: Callable[[float, float], None] | None,
        thumb_handle=None,
    ) -> SendResult:
        """相册被拒后只发视频，避免 500MB+ 传完却整组失败。"""
        attributes = await asyncio.to_thread(extract_video_attributes, video_path)
        prepared, cleanup = _prepare_native_files([video_path])
        send_kwargs = self._send_kwargs(
            task, prepared[0], thumb_handle=thumb_handle, attributes=attributes
        )
        if on_progress is not None:
            send_kwargs["progress_callback"] = on_progress
        try:
            sent_message = await asyncio.wait_for(
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
            if _is_parts_invalid(error):
                return SendOversized(limits.PARTS_INVALID_REASON)
            logger.exception("Telegram 原生单独发视频失败: %s", task.file_path)
            return SendFailed(str(error))
        finally:
            _close_files(cleanup)
        video_message = sent_message[0] if isinstance(sent_message, list) else sent_message
        logger.warning("视频已发送，封面未随相册发送: %s", task.file_path)
        return SendOk(video_message.id)

    async def _upload_thumb(
        self,
        video_path: str,
        page_path: str | None = None,
        need_preview: bool = True,
    ):
        """确保本地有 _thumb.jpg 并上传，返回 InputFile 句柄。

        如果任务策略关闭了预览（need_preview=False），直接跳过不生成也不上传。
        """
        if not need_preview:
            return None
        try:
            from ..utils.video_preview import resolve_thumb_path, VideoThumbnail
            thumb_file = resolve_thumb_path(video_path)
            if not thumb_file.is_file():
                await VideoThumbnail().ensure_thumbnail_async(
                    Path(video_path), Path(page_path) if page_path else None
                )
            if thumb_file.is_file():
                # 走分块上传的连接池。client.upload_file 占用主连接，会和正在进行的并发分块抢同一条链路。
                if self.sender_pool is not None:
                    return await fast_upload_file(
                        self.telegram_client,
                        str(thumb_file),
                        max_workers=1,
                        sender_pool=self.sender_pool,
                    )
                if hasattr(self.telegram_client, "upload_file"):
                    return await self.telegram_client.upload_file(str(thumb_file))
        except Exception:
            logger.debug("上传视频缩略图失败: %s", video_path, exc_info=True)
        return None

    async def _submit_media(
        self, task: Task, handles: list, thumb_handle=None, attributes: list | None = None
    ) -> SendResult:
        attrs = attributes if attributes is not None else await asyncio.to_thread(
            extract_video_attributes, task.artifacts.video_path
        )
        if len(handles) > 1:
            from telethon.tl.types import InputMediaUploadedDocument
            v_media = InputMediaUploadedDocument(
                file=handles[0],
                mime_type="video/mp4",
                attributes=attrs,
                thumb=thumb_handle,
                nosound_video=None,
            )
            files = [v_media, *handles[1:]]
        else:
            files = handles[0]

        sent_messages = await self.telegram_client.send_file(
            **self._send_kwargs(
                task,
                files,
                thumb_handle=None if isinstance(files, list) else thumb_handle,
                attributes=None if isinstance(files, list) else attrs,
            )
        )
        video_message = sent_messages[0] if isinstance(sent_messages, list) else sent_messages
        return SendOk(video_message.id)

    @staticmethod
    def _send_kwargs(task: Task, file, thumb_handle=None, attributes: list | None = None) -> dict:
        """构建 Fast 和原生路径共用的目标、caption、话题参数。"""
        send_kwargs: dict = {
            "entity": task.destination.chat_id,
            "file": file,
            "caption": task.caption or "",
            "force_document": False,
            "supports_streaming": True,
        }
        if task.destination.topic_id is not None:
            send_kwargs["reply_to"] = task.destination.topic_id

        if not isinstance(file, (list, tuple)):
            attrs = attributes if attributes is not None else extract_video_attributes(task.artifacts.video_path)
            if attrs:
                send_kwargs["attributes"] = attrs
            if thumb_handle is not None:
                send_kwargs["thumb"] = thumb_handle

        return send_kwargs


def extract_video_attributes(video_path: str | Path | None) -> list:
    """提取视频时长与宽高，返回 DocumentAttribute 列表以便 Telegram 正确建立播放进度和流式播放。"""
    if not video_path:
        return []
    path_obj = Path(video_path)
    if not path_obj.is_file():
        return []
    suffix = path_obj.suffix.lower()
    if suffix not in _VIDEO_SUFFIXES:
        return []

    probed = _attributes_from_ffprobe(path_obj)
    if probed:
        return probed
    return _attributes_from_hachoir(path_obj)


def _attributes_from_ffprobe(path_obj: Path) -> list:
    """ffprobe 只读容器头，子进程不占 GIL。上传时优先走它，别用 hachoir 扫整文件。"""
    try:
        import json
        import subprocess

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path_obj),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            return []
        data = json.loads(res.stdout)
        streams = data.get("streams", [])
        fmt = data.get("format", {})
        width = int(streams[0].get("width", 1)) if streams else 1
        height = int(streams[0].get("height", 1)) if streams else 1
        duration_value = fmt.get("duration") or (streams[0].get("duration") if streams else None) or 0
        duration = int(float(duration_value))
        if duration <= 0:
            return []
        from telethon.tl.types import DocumentAttributeVideo

        return [
            DocumentAttributeVideo(
                duration=duration,
                w=width,
                h=height,
                supports_streaming=True,
            )
        ]
    except Exception:
        logger.debug("ffprobe 提取视频属性失败: %s", path_obj, exc_info=True)
        return []


def _attributes_from_hachoir(path_obj: Path) -> list:
    try:
        from telethon import utils
        from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

        attrs, _ = utils.get_attributes(str(path_obj), supports_streaming=True)
        video_attrs = [item for item in attrs if isinstance(item, DocumentAttributeVideo)]
        if video_attrs and video_attrs[0].duration > 0 and video_attrs[0].w > 1:
            return [item for item in attrs if not isinstance(item, DocumentAttributeFilename)]
    except Exception:
        logger.debug("hachoir 提取视频属性失败: %s", path_obj, exc_info=True)
    return []



def _rename_input_file(handle, original_path: str):
    """InputFile 的 name 决定 Telethon 推断的 mime；视频统一成 .mp4。"""
    name = telegram_upload_name(original_path)
    if isinstance(handle, InputFileBig):
        return InputFileBig(id=handle.id, parts=handle.parts, name=name)
    if isinstance(handle, InputFile):
        return InputFile(
            id=handle.id,
            parts=handle.parts,
            name=name,
            md5_checksum=handle.md5_checksum,
        )
    return handle


def _prepare_native_files(paths: list[str]) -> tuple[list, list[_NamedFile]]:
    prepared: list = []
    opened: list[_NamedFile] = []
    for path in paths:
        name = telegram_upload_name(path)
        if name == Path(path).name:
            prepared.append(path)
            continue
        renamed = _NamedFile(path, name)
        opened.append(renamed)
        prepared.append(renamed)
    return prepared, opened


def _close_files(files: list[_NamedFile]) -> None:
    for handle in files:
        try:
            handle.close()
        except Exception:
            logger.debug("关闭临时上传文件失败", exc_info=True)


def _is_parts_invalid(error: BaseException) -> bool:
    """FILE_PARTS_INVALID：分片数超过 4000。不要再换连接或换 Bot 重传。"""
    seen: set[int] = set()
    stack: list[BaseException] = [error]
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        name = type(current).__name__.lower().replace("_", "")
        text = str(current).lower().replace(" ", "").replace("_", "")
        if "filepartsinvalid" in name or "filepartsinvalid" in text:
            return True
        if "numberoffilepartsisinvalid" in text:
            return True
        cause = current.__cause__
        if isinstance(cause, BaseException):
            stack.append(cause)
        context = current.__context__
        if isinstance(context, BaseException):
            stack.append(context)
        nested = getattr(current, "exceptions", None)
        if nested:
            stack.extend(item for item in nested if isinstance(item, BaseException))
    return False


def _is_album_invalid(error: BaseException) -> bool:
    if isinstance(error, MediaInvalidError):
        return True
    text = str(error).lower()
    return "media invalid" in text or "sendmultimedia" in text.replace(" ", "")


def _is_disconnect_error(error: BaseException) -> bool:
    seen: set[int] = set()
    stack: list[BaseException] = [error]
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
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(current, ConnectionError):
            return True
        text = str(current).lower()
        if any(item in text for item in needles):
            return True
        cause = current.__cause__
        if isinstance(cause, BaseException):
            stack.append(cause)
        context = current.__context__
        if isinstance(context, BaseException):
            stack.append(context)
        nested = getattr(current, "exceptions", None)
        if nested:
            stack.extend(item for item in nested if isinstance(item, BaseException))
    return False


def _classify_send_error(error: BaseException, prefix: str) -> SendResult:
    """将消息提交阶段异常转换为 Transport 统一结果，避免重复提交。"""
    if isinstance(error, FloodWaitError):
        return SendRetryLater(error.seconds)
    if isinstance(error, TimeoutError):
        return SendFailed(f"{prefix}: 请求超时，未自动重发")
    if _is_disconnect_error(error):
        return SendDisconnected(str(error))
    if _is_parts_invalid(error):
        return SendOversized(limits.PARTS_INVALID_REASON)
    logger.exception("%s: %s", prefix, error)
    return SendFailed(str(error))
