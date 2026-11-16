import asyncio
import hashlib
from pathlib import Path

import pytest

from telethon.tl.types import InputFileBig

from src.adapters.sessions import RECONNECT_MAX_WAIT, reconnect_wait_seconds
from src.adapters.telegram_transport import TelegramTransport
from src.domain.task import AfterSuccess, Task, TaskArtifacts, TaskDestination, TaskPolicy, TaskStatus
from src.ports.transport import SendFailed
from src.utils.FastTelethon import CHUNK_SIZE, UploadInterrupted, upload_file


def test_backoff_grows_and_caps_at_30() -> None:
    assert reconnect_wait_seconds(1) == 1.0
    assert reconnect_wait_seconds(2) == 2.0
    assert reconnect_wait_seconds(3) == 4.0
    assert reconnect_wait_seconds(4) == 8.0
    assert reconnect_wait_seconds(5) == RECONNECT_MAX_WAIT
    assert reconnect_wait_seconds(9) == RECONNECT_MAX_WAIT


class _CursorFile:
    """用 seek/tell 假装很大，只在真正 read 时记位置。"""

    def __init__(self, size: int, name: str = "big.mp4") -> None:
        self.name = name
        self._size = size
        self._pos = 0
        self.reads: list[tuple[int, int]] = []
        self.seeks: list[tuple[int, int]] = []

    def seek(self, offset: int, whence: int = 0) -> int:
        self.seeks.append((offset, whence))
        if whence == 0:
            self._pos = offset
        elif whence == 2:
            self._pos = self._size + offset
        else:
            self._pos += offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        self.reads.append((self._pos, size))
        if self._pos >= self._size:
            return b""
        take = self._size - self._pos if size is None or size < 0 else min(size, self._size - self._pos)
        self._pos += take
        return b"x" * take

    def close(self) -> None:
        return None


class _AckSender:
    def __init__(self) -> None:
        self.parts: list[int] = []

    def send(self, request):
        self.parts.append(request.file_part)
        future = asyncio.get_running_loop().create_future()
        future.set_result(True)
        return future


class _Pool:
    def __init__(self, sender) -> None:
        self.sender = sender

    async def acquire(self, count: int):
        return [self.sender]

    async def release(self, senders) -> None:
        return None


async def test_big_file_skip_seeks_once_and_counts_bytes(monkeypatch) -> None:
    monkeypatch.setattr("src.utils.FastTelethon.PROGRESS_INTERVAL_SECONDS", 0)
    size = 10 * 1024 * 1024 + 100
    handle = _CursorFile(size)
    skipped = 20
    seen: list[int] = []
    sender = _AckSender()
    result = await upload_file(
        None,
        handle,
        progress_callback=lambda current, _total: seen.append(current),
        max_workers=1,
        sender_pool=_Pool(sender),
        file_id=99,
        skip_parts=set(range(skipped)),
    )
    resume_at = skipped * CHUNK_SIZE
    assert all(pos >= resume_at for pos, _size in handle.reads)
    assert (resume_at, 0) in handle.seeks
    assert sender.parts == [skipped]
    assert seen[0] == CHUNK_SIZE
    assert resume_at in seen
    assert seen[-1] == size
    assert isinstance(result, InputFileBig)
    assert result.id == 99
    assert result.parts == skipped + 1


async def test_small_file_skip_parts_still_hashes_everything(tmp_path: Path) -> None:
    payload = b"md5-me-please"
    path = tmp_path / "small.mp4"
    path.write_bytes(payload)
    sender = _AckSender()
    result = await upload_file(
        None,
        str(path),
        max_workers=1,
        sender_pool=_Pool(sender),
        file_id=99,
        skip_parts={0},
    )
    assert sender.parts == [0]
    assert result.md5_checksum == hashlib.md5(payload).hexdigest()


class _HangAfterFirst:
    def send(self, request):
        future = asyncio.get_running_loop().create_future()
        if request.file_part == 0:
            future.set_result(True)
        return future


async def test_part_ack_timeout_keeps_finished_parts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.utils.FastTelethon.PART_ACK_TIMEOUT_SECONDS", 0.05)
    path = tmp_path / "two-parts.bin"
    path.write_bytes(b"z" * (CHUNK_SIZE + 10))
    with pytest.raises(UploadInterrupted) as caught:
        await upload_file(
            None,
            str(path),
            max_workers=1,
            sender_pool=_Pool(_HangAfterFirst()),
        )
    assert caught.value.done == {0}
    assert caught.value.total_parts == 2
    assert "timeout" in str(caught.value)
