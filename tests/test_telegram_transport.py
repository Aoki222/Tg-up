import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

from telethon.tl.types import InputFileBig

from src.adapters.telegram_transport import (
    TelegramTransport,
    telegram_upload_name,
    _NamedFile,
    _is_album_invalid,
    _rename_input_file,
)
from src.domain.task import AfterSuccess, Task, TaskArtifacts, TaskDestination, TaskPolicy, TaskStatus
from src.ports.transport import SendDisconnected, SendFailed, SendOk, SendOversized
from src.utils.FastTelethon import UploadInterrupted, _consume_future_exception, describe_taskgroup_error


def test_telegram_upload_name_maps_m4v_to_mp4() -> None:
    assert telegram_upload_name("/宵夜/高跟黑丝自慰.M4V") == "高跟黑丝自慰.mp4"
    assert telegram_upload_name("clip.mp4") == "clip.mp4"
    assert telegram_upload_name("cover.jpg") == "cover.jpg"


def test_rename_input_file_uses_mp4_name() -> None:
    handle = InputFileBig(id=1, parts=3, name="clip.M4V")
    renamed = _rename_input_file(handle, "/data/clip.M4V")
    assert renamed.name == "clip.mp4"
    assert renamed.id == 1
    assert renamed.parts == 3


def test_describe_taskgroup_error_unwraps_sub_exceptions() -> None:
    group = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [
            AttributeError("'TelegramClient' object has no attribute '_borrow_sender'"),
            AttributeError("'TelegramClient' object has no attribute '_borrow_sender'"),
        ],
    )
    text = describe_taskgroup_error(group)
    assert "AttributeError" in text
    assert "_borrow_sender" in text


def test_named_file_streams_in_chunks(tmp_path: Path) -> None:
    path = tmp_path / "clip.mov"
    payload = b"abcdefghij" * 50
    path.write_bytes(payload)
    handle = _NamedFile(str(path), "clip.mp4")
    try:
        assert handle.seekable()
        assert handle.seek(0, os.SEEK_END) == len(payload)
        handle.seek(0)
        assert handle.read(-1) == payload
    finally:
        handle.close()


def test_album_invalid_detects_media_invalid() -> None:
    assert _is_album_invalid(Exception("Media invalid (caused by SendMultiMediaRequest)"))


def _task(video: Path, cover: Path | None = None) -> Task:
    return Task(
        id=1,
        file_path=str(video),
        file_name=video.name,
        file_size=video.stat().st_size,
        destination=TaskDestination(chat_id=-100, dest_id="-100"),
        artifacts=TaskArtifacts(video_path=str(video), page_path=str(cover) if cover else None),
        policy=TaskPolicy(need_preview=bool(cover), after_success=AfterSuccess.KEEP, max_retries=3),
        status=TaskStatus.UPLOADING,
    )


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_lists = True

    def is_connected(self) -> bool:
        return True

    async def send_file(self, **kwargs):
        self.calls.append(kwargs)
        file = kwargs["file"]
        if isinstance(file, list) and self.fail_lists:
            self.fail_lists = False
            raise RuntimeError("Media invalid (caused by SendMultiMediaRequest)")
        return SimpleNamespace(id=99)


async def test_native_album_invalid_falls_back_to_video_only(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.M4V"
    cover = tmp_path / "clip_single.jpg"
    video.write_bytes(b"video-bytes")
    cover.write_bytes(b"jpeg")

    async def fail_fast(*_args, **_kwargs):
        raise RuntimeError("unhandled errors in a TaskGroup (6 sub-exceptions)")

    monkeypatch.setattr("src.adapters.telegram_transport.fast_upload_file", fail_fast)
    client = _FakeClient()
    transport = TelegramTransport(client)
    result = await transport.send(_task(video, cover), timeout_seconds=30)
    assert isinstance(result, SendOk)
    assert result.message_id == 99
    assert any(isinstance(call["file"], list) for call in client.calls)
    video_only = [call for call in client.calls if not isinstance(call["file"], list)]
    assert video_only
    sent = video_only[-1]["file"]
    name = sent if isinstance(sent, str) else getattr(sent, "name", "")
    assert str(name).endswith(".mp4")


async def test_file_parts_invalid_does_not_fall_back_to_native(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "huge.mp4"
    video.write_bytes(b"video-bytes")
    calls = {"fast": 0}

    async def fail_fast(*_args, **_kwargs):
        calls["fast"] += 1
        raise RuntimeError("FilePartsInvalidError: The number of file parts is invalid")

    monkeypatch.setattr("src.adapters.telegram_transport.fast_upload_file", fail_fast)
    client = _FakeClient()
    result = await TelegramTransport(client).send(_task(video), timeout_seconds=30)
    assert isinstance(result, SendOversized)
    assert calls["fast"] == 1
    assert client.calls == []


async def test_disconnect_does_not_retry_or_fall_back_to_native(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes")
    calls = {"fast": 0}

    async def fail_fast(*_args, **_kwargs):
        calls["fast"] += 1
        raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr("src.adapters.telegram_transport.fast_upload_file", fail_fast)
    client = _FakeClient()
    result = await TelegramTransport(client).send(_task(video), timeout_seconds=30)
    assert isinstance(result, SendDisconnected)
    assert calls["fast"] == 1
    assert client.calls == []


async def test_reconnect_resumes_finished_parts(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes" * 20)
    seen: list[tuple[int | None, set[int]]] = []

    async def fake_upload(*_args, **kwargs):
        file_id = kwargs.get("file_id")
        skip_parts = set(kwargs.get("skip_parts") or ())
        seen.append((file_id, skip_parts))
        if len(seen) == 1:
            raise UploadInterrupted(
                99,
                10,
                video.stat().st_size,
                {0, 1, 2, 3},
                ConnectionResetError(104, "Connection reset by peer"),
            )
        return InputFileBig(id=file_id or 1, parts=10, name="clip.mp4")

    monkeypatch.setattr("src.adapters.telegram_transport.fast_upload_file", fake_upload)
    client = _FakeClient()
    transport = TelegramTransport(client)
    first = await transport.send(_task(video), timeout_seconds=30)
    assert isinstance(first, SendDisconnected)
    assert client.calls == []
    second = await transport.send(_task(video), timeout_seconds=30)
    assert isinstance(second, SendOk)
    assert seen[0] == (None, set())
    assert seen[1] == (99, {0, 1, 2, 3})


async def test_consume_future_exception_marks_retrieved() -> None:
    future = asyncio.get_running_loop().create_future()
    future.set_exception(ConnectionResetError(104, "Connection reset by peer"))
    _consume_future_exception(future)
    assert isinstance(future.exception(), ConnectionResetError)


async def test_missing_file_fails_without_sending(tmp_path: Path) -> None:
    missing = tmp_path / "gone.webm"
    task = Task(
        id=1,
        file_path=str(missing),
        file_name=missing.name,
        file_size=8,
        destination=TaskDestination(chat_id=-100, dest_id="-100"),
        artifacts=TaskArtifacts(video_path=str(missing)),
        policy=TaskPolicy(need_preview=False, after_success=AfterSuccess.KEEP, max_retries=3),
        status=TaskStatus.UPLOADING,
    )
    client = _FakeClient()
    result = await TelegramTransport(client).send(task, timeout_seconds=30)
    assert isinstance(result, SendFailed)
    assert result.retryable is False
    assert "文件不存在" in result.reason
    assert client.calls == []
