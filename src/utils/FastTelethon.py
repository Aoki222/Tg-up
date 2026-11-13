import asyncio
import hashlib
import inspect
import math
import os
import time
from typing import Any, AsyncGenerator, Awaitable, BinaryIO, Callable, Optional, Union

from telethon import TelegramClient, helpers
from telethon.network import MTProtoSender
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import (
    GetFileRequest,
    SaveBigFilePartRequest,
    SaveFilePartRequest,
)
from telethon.tl.types import (
    Document,
    InputDocumentFileLocation,
    InputFile,
    InputFileBig,
    InputFileLocation,
    InputPeerPhotoFileLocation,
    InputPhotoFileLocation,
    TypeInputFile,
)

from ..logger import get_logger

logger = get_logger(__name__)

# MTProto 规范中单块的最大物理上限：512 KB
CHUNK_SIZE = 512 * 1024
# 同一条连接上同时在飞的分片。1 表示发出去就干等应答，高延迟时带宽填不满。
# 4 块约 2MB。CPU 还有空时用它把窗口垫大，而不是先把连接数堆很高。
PARTS_PER_CONNECTION = 4
# 进度回调走控制台和 SSE，不必每个 512KB 都打一次。
PROGRESS_INTERVAL_SECONDS = 0.2


def active_crypto_backend() -> str:
    """Telethon 有 cryptg 就用 C 做 AES-IGE，否则退回纯 Python pyaes。"""
    try:
        import cryptg  # noqa: F401
    except ImportError:
        return "pyaes"
    return "cryptg"

TypeLocation = Union[
    Document,
    InputDocumentFileLocation,
    InputPeerPhotoFileLocation,
    InputPhotoFileLocation,
    InputFileLocation,
]


def describe_taskgroup_error(error: BaseException) -> str:
    """TaskGroup 把真实原因包进 ExceptionGroup，日志里要展开子异常。"""
    if isinstance(error, BaseExceptionGroup):
        parts = [describe_taskgroup_error(item) for item in error.exceptions]
        return "; ".join(parts) or str(error)
    text = str(error).strip()
    name = type(error).__name__
    return f"{name}: {text}" if text else name


def _consume_future_exception(future: asyncio.Future) -> None:
    """连接断开时 Telethon 会把异常写进 Future。取消上传任务时要把这个异常读出来。"""

    def _read(item: asyncio.Future) -> None:
        if item.cancelled():
            return
        item.exception()

    if future.done():
        _read(future)
    else:
        future.add_done_callback(_read)


def _is_connection_failure(error: BaseException) -> bool:
    seen: set[int] = set()
    stack: list[BaseException] = [error]
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(current, ConnectionError):
            return True
        text = str(current).lower()
        if any(
            item in text
            for item in (
                "disconnect",
                "not connected",
                "connection reset",
                "connection closed",
                "server closed",
            )
        ):
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


class UploadInterrupted(Exception):
    """分块传到一半连接断了。file_id 和已完成分片可以在同一账号上接着传。"""

    def __init__(
        self,
        file_id: int,
        total_parts: int,
        file_size: int,
        done: set[int],
        cause: BaseException,
    ) -> None:
        super().__init__(describe_taskgroup_error(cause))
        self.file_id = file_id
        self.total_parts = total_parts
        self.file_size = file_size
        self.done = set(done)
        self.cause = cause


async def _create_parallel_sender(client: TelegramClient) -> MTProtoSender:
    """同一 DC 上再建一条独立 TCP，复用当前 session 的 auth_key。"""
    dc_id = client.session.dc_id
    dc = await client._get_dc(dc_id)
    sender = MTProtoSender(
        client.session.auth_key,
        loggers=client._log,
        auto_reconnect=False,
    )
    await sender.connect(
        client._connection(
            dc.ip_address,
            dc.port,
            dc.id,
            loggers=client._log,
            proxy=client._proxy,
            local_addr=getattr(client, "_local_addr", None),
        )
    )
    return sender


async def _open_upload_senders(client: TelegramClient, count: int) -> tuple[list[MTProtoSender], list[MTProtoSender]]:
    """尽量打开 count 条额外连接；一条都建不出时退回主连接，不要 disconnect 主连接。"""
    extra: list[MTProtoSender] = []
    for index in range(max(1, count)):
        try:
            extra.append(await _create_parallel_sender(client))
        except Exception as error:
            logger.warning("额外上传连接建立失败 (%s/%s): %s", index + 1, count, error)
            break
    if extra:
        return extra, extra
    main = getattr(client, "_sender", None)
    if main is None:
        raise RuntimeError("Telegram client 没有可用的上传连接")
    return [main], []


class ParallelTransferrer:
    def __init__(self, client: TelegramClient, dc_id: Optional[int] = None):
        self.client = client
        self.loop = self.client.loop
        self.dc_id = dc_id or self.client.session.dc_id
        self.auth_key = (
            None
            if dc_id and self.client.session.dc_id != dc_id
            else self.client.session.auth_key
        )
        self.sender: Optional[MTProtoSender] = None

    async def _cleanup(self):
        if self.sender:
            await self.sender.disconnect()
            self.sender = None

    @staticmethod
    def _get_connection_count(file_size: int, max_count: int = 4) -> int:
        """根据文件大小自动计算适合的并发连接数"""
        if file_size > 100 * 1024 * 1024:  # > 100MB
            return max_count
        elif file_size > 20 * 1024 * 1024:   # > 20MB
            return min(max_count, 3)
        return min(max_count, 2)

    async def _init_download(
        self, connections: int, location: TypeLocation, part_size: int
    ) -> tuple[int, AsyncGenerator[bytes, None]]:
        # 针对跨 DC 下载进行权限认证并初始化连接池
        if not self.auth_key:
            dc = await self.client._get_dc(self.dc_id)
            export_auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            self.sender = MTProtoSender(self.auth_key, loggers=self.client._log)
            await self.sender.connect(self.client._connection(
                dc.ip_address, dc.port, dc.id, loggers=self.client._log
            ))
            await self.sender.send(ImportAuthorizationRequest(
                id=export_auth.id, bytes=export_auth.bytes
            ))
        else:
            self.sender = self.client._sender

        file_size = location.size if hasattr(location, "size") else 0
        part_count = math.ceil(file_size / part_size) if file_size else 0

        async def generator():
            # 建立多连接队列并发拉取
            queue = asyncio.Queue(maxsize=connections)

            async def worker(part_idx: int):
                sender = await self.client._borrow_exported_sender(self.dc_id)
                try:
                    res = await sender.send(
                        GetFileRequest(
                            location=location,
                            offset=part_idx * part_size,
                            limit=part_size,
                        )
                    )
                    await queue.put((part_idx, res.bytes))
                finally:
                    await self.client._return_exported_sender(sender)

            # 派发下载切片
            for i in range(part_count):
                self.loop.create_task(worker(i))

            parts = {}
            next_part = 0
            while next_part < part_count:
                idx, data = await queue.get()
                parts[idx] = data
                while next_part in parts:
                    yield parts.pop(next_part)
                    next_part += 1

        return file_size, generator()


class FastTelethon:
    @staticmethod
    async def upload_file(
        client: TelegramClient,
        file: Union[str, BinaryIO],
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
        max_workers: int = 6,
        sender_pool: Optional[Any] = None,
        file_id: Optional[int] = None,
        skip_parts: Optional[set[int]] = None,
    ) -> TypeInputFile:
        """
        极速多连接并发分块上传
        """
        # 处理传入的是文件路径还是文件对象
        if isinstance(file, str):
            file_size = os.path.getsize(file)
            file_name = os.path.basename(file)
            file_handle = open(file, "rb")
            should_close = True
        else:
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            file_name = getattr(file, "name", "file")
            file_handle = file
            should_close = False

        total_parts = math.ceil(file_size / CHUNK_SIZE) if file_size else 1
        is_big = file_size > 10 * 1024 * 1024  # > 10MB
        done_parts = {index for index in (skip_parts or set()) if 0 <= index < total_parts}
        if file_id is None or not done_parts:
            file_id = helpers.generate_random_long()
            done_parts = set()
        completed: set[int] = set(done_parts)
        hash_md5 = hashlib.md5()

        desired_workers = min(total_parts, max(1, int(max_workers)))
        owned_senders: list[MTProtoSender] = []
        is_from_pool = False

        if sender_pool is not None:
            senders = []
            # 池子被别的文件占满时等待空位。退回主连接会和 send_file、心跳抢同一条链路。
            for _ in range(40):
                senders = await sender_pool.acquire(desired_workers)
                if senders:
                    break
                await asyncio.sleep(0.05)
            if not senders:
                raise RuntimeError("上传连接都在忙")
            is_from_pool = True
        else:
            senders, owned_senders = await _open_upload_senders(client, desired_workers)

        worker_count = len(senders)
        logger.info(
            "FastTelethon 分块上传 name=%s size=%s parts=%s connections=%s depth=%s crypto=%s (pool=%s)",
            file_name,
            file_size,
            total_parts,
            worker_count,
            PARTS_PER_CONNECTION,
            active_crypto_backend(),
            is_from_pool,
        )
        queue: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue(
            maxsize=worker_count * PARTS_PER_CONNECTION
        )
        uploaded_bytes = 0
        lock = asyncio.Lock()
        last_report_at = 0.0
        last_reported = -1

        async def report_progress(current: int) -> None:
            if progress_callback is None:
                return
            if inspect.iscoroutinefunction(progress_callback):
                await progress_callback(current, file_size)
            else:
                progress_callback(current, file_size)

        async def maybe_report(current: int, *, force: bool = False) -> None:
            """进度节流。锁里只改计数，回调放到锁外，避免堵住正在发包的连接。"""
            nonlocal last_report_at, last_reported
            if progress_callback is None:
                return
            async with lock:
                if current == last_reported:
                    return
                now = time.monotonic()
                if (
                    not force
                    and last_reported >= 0
                    and (now - last_report_at) < PROGRESS_INTERVAL_SECONDS
                ):
                    return
                last_report_at = now
                last_reported = current
            await report_progress(current)

        async def upload_worker(sender: MTProtoSender) -> None:
            nonlocal uploaded_bytes
            inflight: list[tuple[int, int, Any]] = []

            def enqueue_send(part_idx: int, chunk_data: bytes) -> None:
                if is_big:
                    request = SaveBigFilePartRequest(
                        file_id=file_id,
                        file_part=part_idx,
                        file_total_parts=total_parts,
                        bytes=chunk_data,
                    )
                else:
                    request = SaveFilePartRequest(
                        file_id=file_id,
                        file_part=part_idx,
                        bytes=chunk_data,
                    )
                inflight.append((part_idx, len(chunk_data), sender.send(request)))

            async def ack_oldest() -> None:
                nonlocal uploaded_bytes
                part_idx, size, pending = inflight.pop(0)
                try:
                    result = await pending
                except asyncio.CancelledError:
                    if asyncio.isfuture(pending):
                        _consume_future_exception(pending)
                    raise
                if asyncio.isfuture(pending):
                    _consume_future_exception(pending)
                if not result:
                    raise RuntimeError(f"分块 {part_idx}/{total_parts} 上传失败")
                async with lock:
                    completed.add(part_idx)
                    uploaded_bytes += size
                    current = uploaded_bytes
                await maybe_report(current)

            try:
                while True:
                    while len(inflight) < PARTS_PER_CONNECTION:
                        item = await queue.get()
                        queue.task_done()
                        if item is None:
                            while inflight:
                                await ack_oldest()
                            async with lock:
                                current = uploaded_bytes
                            await maybe_report(current, force=True)
                            return
                        part_idx, chunk_data = item
                        enqueue_send(part_idx, chunk_data)
                    await ack_oldest()
            finally:
                for _, _, pending in inflight:
                    if asyncio.isfuture(pending):
                        _consume_future_exception(pending)

        async def produce() -> None:
            nonlocal uploaded_bytes
            for part_index in range(total_parts):
                # 磁盘读放进线程，事件循环才能继续收应答、把下一块发出去。
                chunk = await asyncio.to_thread(file_handle.read, CHUNK_SIZE)
                if not chunk and file_size:
                    raise OSError("读取文件分块失败")
                if not is_big and chunk:
                    hash_md5.update(chunk)
                if part_index in done_parts:
                    async with lock:
                        uploaded_bytes += len(chunk)
                        current = uploaded_bytes
                    await maybe_report(current)
                    continue
                await queue.put((part_index, chunk or b""))
            for _ in range(worker_count):
                await queue.put(None)

        try:
            try:
                async with asyncio.TaskGroup() as group:
                    for sender in senders:
                        group.create_task(upload_worker(sender))
                    group.create_task(produce())
            except BaseExceptionGroup as error:
                if _is_connection_failure(error):
                    raise UploadInterrupted(
                        file_id,
                        total_parts,
                        file_size,
                        completed,
                        error,
                    ) from error
                raise RuntimeError(describe_taskgroup_error(error)) from error
        finally:
            if should_close:
                file_handle.close()
            if is_from_pool and sender_pool is not None:
                await sender_pool.release(senders)
            else:
                for sender in owned_senders:
                    try:
                        await sender.disconnect()
                    except Exception:
                        logger.debug("关闭额外上传连接失败", exc_info=True)

        # 返回 Telegram 官方需要的 InputFile 凭证对象
        if is_big:
            return InputFileBig(id=file_id, parts=total_parts, name=file_name)
        else:
            return InputFile(
                id=file_id,
                parts=total_parts,
                name=file_name,
                md5_checksum=hash_md5.hexdigest(),
            )

    @staticmethod
    async def download_file(
        client: TelegramClient,
        location: TypeLocation,
        out: Union[str, BinaryIO],
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
        max_workers: int = 4,
    ) -> Union[str, BinaryIO]:
        """
        极速多连接并发分块下载
        """
        transferrer = ParallelTransferrer(client)
        size, generator = await transferrer._init_download(
            max_workers, location, CHUNK_SIZE
        )

        if isinstance(out, str):
            out_file = open(out, "wb")
            should_close = True
        else:
            out_file = out
            should_close = False

        downloaded_bytes = 0
        try:
            async for part in generator:
                out_file.write(part)
                downloaded_bytes += len(part)
                if progress_callback:
                    if inspect.iscoroutinefunction(progress_callback):
                        await progress_callback(downloaded_bytes, size)
                    else:
                        progress_callback(downloaded_bytes, size)
        finally:
            if should_close:
                out_file.close()
            await transferrer._cleanup()

        return out


# 便捷别名导出，方便直接 import
upload_file = FastTelethon.upload_file
download_file = FastTelethon.download_file
create_parallel_sender = _create_parallel_sender
describe_error = describe_taskgroup_error
