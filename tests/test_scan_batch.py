"""启动扫盘边走边入队，同时入库的文件有上限。"""

import asyncio
import os
import time
from pathlib import Path

from src.domain.upload_settings import FolderRoute
from src.pipeline.discover.scan import iter_existing_files
from src.pipeline.ingest.ingestor import FileIngestor
from tests.test_ingestor import FakeHub, FakeRepo, FakeRescheduler, _settings


def _old_videos(directory: Path, count: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    past = time.time() - 3600
    for index in range(count):
        path = directory / f"{index}.mp4"
        path.write_bytes(b"x")
        os.utime(path, (past, past))


async def test_walk_waits_when_the_queue_is_full(tmp_path: Path) -> None:
    root = tmp_path / "in"
    _old_videos(root, 30)
    queue: asyncio.Queue[Path] = asyncio.Queue(maxsize=2)
    walked = 0

    async def produce() -> None:
        nonlocal walked
        for path in iter_existing_files(root, frozenset({".mp4"})):
            walked += 1
            await queue.put(path)

    producer = asyncio.create_task(produce())
    await asyncio.sleep(0.05)
    assert not producer.done()
    assert walked <= queue.maxsize + 1
    assert queue.qsize() <= queue.maxsize

    received: list[Path] = []
    while len(received) < 30:
        received.append(await asyncio.wait_for(queue.get(), timeout=2))
    await asyncio.wait_for(producer, timeout=2)
    assert {path.name for path in received} == {f"{index}.mp4" for index in range(30)}


async def test_consume_keeps_inflight_within_concurrency(tmp_path: Path, monkeypatch) -> None:
    async def instant(file_path: Path, **_kwargs) -> int | None:
        return file_path.stat().st_size if file_path.is_file() else None

    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", instant)
    root = tmp_path / "in"
    _old_videos(root, 12)
    repo = FakeRepo()
    hub = FakeHub(
        _settings(
            observer_paths=(root,),
            routes=(FolderRoute(path=root.resolve(), chat_id=-100, dest_id="-100"),),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=2)
    queue: asyncio.Queue[Path | None] = asyncio.Queue(maxsize=3)
    peak_inflight = 0
    original = ingestor.handle_new_file

    async def tracked(file_path: Path, caption: str = "") -> int | None:
        nonlocal peak_inflight
        peak_inflight = max(peak_inflight, len(ingestor._inflight))
        return await original(file_path, caption)

    ingestor.handle_new_file = tracked  # type: ignore[method-assign]
    consumer = asyncio.create_task(ingestor.consume(queue))

    async def produce() -> None:
        for path in iter_existing_files(root, frozenset({".mp4"})):
            await queue.put(path)
        await queue.put(None)

    await asyncio.wait_for(produce(), timeout=5)
    await asyncio.wait_for(consumer, timeout=5)
    assert peak_inflight <= 2
    assert len(repo.tasks) == 12
    assert queue.qsize() == 0
