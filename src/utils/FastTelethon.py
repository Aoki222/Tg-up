import asyncio
import hashlib
import inspect
import math
import os
from typing import AsyncGenerator, Awaitable, BinaryIO, Callable, Optional, Union

from telethon import TelegramClient, helpers, utils
from telethon.crypto import AuthKey
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

# MTProto 规范中单块的最大物理上限：512 KB
CHUNK_SIZE = 512 * 1024

TypeLocation = Union[
    Document,
    InputDocumentFileLocation,
    InputPeerPhotoFileLocation,
    InputPhotoFileLocation,
    InputFileLocation,
]


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
                sender = await self.client._borrow_sender(self.dc_id)
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
                    await self.client._return_sender(sender)

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

        total_parts = math.ceil(file_size / CHUNK_SIZE)
        is_big = file_size > 10 * 1024 * 1024  # > 10MB
        file_id = helpers.generate_random_long()
        hash_md5 = hashlib.md5()

        semaphore = asyncio.Semaphore(max_workers)
        uploaded_bytes = 0
        lock = asyncio.Lock()

        async def upload_worker(part_idx: int, chunk_data: bytes):
            nonlocal uploaded_bytes
            async with semaphore:
                # 借用会话池的 TCP 发送管道
                sender = await client._borrow_sender(client.session.dc_id)
                try:
                    if is_big:
                        req = SaveBigFilePartRequest(
                            file_id=file_id,
                            file_part=part_idx,
                            file_total_parts=total_parts,
                            bytes=chunk_data,
                        )
                    else:
                        req = SaveFilePartRequest(
                            file_id=file_id,
                            file_part=part_idx,
                            bytes=chunk_data,
                        )
                    await sender.send(req)
                finally:
                    await client._return_sender(sender)

                # 回调进度处理
                if progress_callback:
                    async with lock:
                        uploaded_bytes += len(chunk_data)
                        if inspect.iscoroutinefunction(progress_callback):
                            await progress_callback(uploaded_bytes, file_size)
                        else:
                            progress_callback(uploaded_bytes, file_size)

        tasks = []
        try:
            for part_index in range(total_parts):
                chunk = file_handle.read(CHUNK_SIZE)
                if not is_big:
                    hash_md5.update(chunk)
                tasks.append(upload_worker(part_index, chunk))

            await asyncio.gather(*tasks)
        finally:
            if should_close:
                file_handle.close()

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