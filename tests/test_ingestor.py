import asyncio
import os
import time
from pathlib import Path
from src.domain.task import AfterSuccess, TaskPolicy
from src.domain.upload_settings import PreviewMode, UploadSettings
from src.pipeline.ingest.ingestor import FileIngestor, wait_until_file_stable


def _settings(**kwargs) -> UploadSettings:
    base = dict(
        chat_id=-100,
        observer_paths=(Path("download"),),
        page_dir=Path("page"),
        archive_dir=Path("uploaded"),
        preview=PreviewMode.OFF,
        topic_creation_enabled=False,
        after_success=AfterSuccess.KEEP,
        concurrency=1,
        max_retries=3,
        upload_timeout_seconds=1200,
        assigned_timeout_seconds=600,
        stable_timeout_seconds=30,
        watch_extensions=frozenset({".mp4"}),
    )
    base.update(kwargs)
    return UploadSettings(**base)


class FakeHub:
    def __init__(self, settings: UploadSettings):
        self._settings = settings
        self.policies: dict[int, TaskPolicy] = {}

    def get(self) -> UploadSettings:
        return self._settings

    def policy_for_new_task(self, need_preview: bool) -> TaskPolicy:
        return TaskPolicy(
            need_preview=need_preview,
            after_success=self._settings.after_success,
            max_retries=self._settings.max_retries,
        )

    def remember_policy(self, task_id: int, policy: TaskPolicy) -> None:
        self.policies[task_id] = policy


class FakeRepo:
    def __init__(self):
        self.tasks: list[dict] = []
        self.active: dict[str, int] = {}

    async def find_active_by_file_path(self, file_path: str) -> int | None:
        return self.active.get(file_path)

    async def add_task(self, **kwargs) -> int:
        task_id = len(self.tasks) + 1
        self.tasks.append(kwargs)
        self.active[kwargs["file_path"]] = task_id
        return task_id

    async def update_preview(self, task_id: int, page_path, success: bool, error_message: str = "") -> None:
        return None


class FakeRescheduler:
    def __init__(self):
        self.calls = 0

    def request_reschedule(self) -> None:
        self.calls += 1


def _ingestor(tmp_path: Path) -> tuple[FileIngestor, FakeRepo]:
    repo = FakeRepo()
    hub = FakeHub(_settings())
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=8)
    return ingestor, repo


def _video(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"video")
    return path


async def test_old_file_is_stable_immediately(tmp_path: Path) -> None:
    path = _video(tmp_path, "old.mp4")
    past = time.time() - 60
    os.utime(path, (past, past))
    started = time.monotonic()
    size = await wait_until_file_stable(path, check_interval=2.0, stable_rounds=3, timeout_seconds=5)
    assert size == 5
    assert time.monotonic() - started < 0.2


async def test_fresh_file_counts_first_stat(tmp_path: Path, monkeypatch) -> None:
    path = _video(tmp_path, "fresh.mp4")
    frozen = path.stat().st_mtime
    monkeypatch.setattr("src.pipeline.ingest.ingestor.time.time", lambda: frozen)
    started = time.monotonic()
    size = await wait_until_file_stable(path, check_interval=0.05, stable_rounds=3, timeout_seconds=2)
    assert size == 5
    elapsed = time.monotonic() - started
    assert 0.07 <= elapsed < 0.5


async def test_missing_file_returns_none(tmp_path: Path) -> None:
    size = await wait_until_file_stable(tmp_path / "gone.mp4", check_interval=0.01, timeout_seconds=1)
    assert size is None


async def test_consume_waits_in_parallel(tmp_path: Path, monkeypatch) -> None:
    async def slow_stable(file_path: Path, **_kwargs) -> int:
        await asyncio.sleep(0.25)
        return file_path.stat().st_size

    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", slow_stable)
    ingestor, repo = _ingestor(tmp_path)
    queue: asyncio.Queue[Path | None] = asyncio.Queue()
    await queue.put(_video(tmp_path, "a.mp4"))
    await queue.put(_video(tmp_path, "b.mp4"))
    await queue.put(None)
    started = time.monotonic()
    await ingestor.consume(queue)
    elapsed = time.monotonic() - started
    assert len(repo.tasks) == 2
    assert elapsed < 0.45


async def test_duplicate_path_skipped_while_inflight(tmp_path: Path, monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_stable(file_path: Path, **_kwargs) -> int:
        started.set()
        await release.wait()
        return file_path.stat().st_size

    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", blocked_stable)
    ingestor, repo = _ingestor(tmp_path)
    path = _video(tmp_path, "same.mp4")
    first = asyncio.create_task(ingestor.handle_new_file(path))
    await started.wait()
    second = await ingestor.handle_new_file(path)
    assert second is None
    assert repo.tasks == []
    release.set()
    task_id = await first
    assert task_id == 1
    assert len(repo.tasks) == 1


async def test_insert_lock_does_not_cover_topic_wait(tmp_path: Path) -> None:
    """建话题等待时，另一个目录的文件仍能入库。"""
    topic_started = asyncio.Event()
    topic_release = asyncio.Event()
    created: list[str] = []

    class SlowTopics:
        async def get_or_create_topic(self, fold_path: str, folder_name: str, chat_id: int) -> int:
            created.append(folder_name)
            if folder_name == "dir_a":
                topic_started.set()
                await topic_release.wait()
            return 1

    repo = FakeRepo()
    hub = FakeHub(_settings(topic_creation_enabled=True))
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, topic_creator=SlowTopics(), concurrency=8)

    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    file_a = dir_a / "a.mp4"
    file_b = dir_b / "b.mp4"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")
    past = time.time() - 60
    os.utime(file_a, (past, past))
    os.utime(file_b, (past, past))

    first = asyncio.create_task(ingestor.handle_new_file(file_a))
    await topic_started.wait()
    second_id = await asyncio.wait_for(ingestor.handle_new_file(file_b), timeout=1)
    assert second_id == 1
    assert [row["folder_name"] for row in repo.tasks] == ["dir_b"]
    topic_release.set()
    first_id = await first
    assert first_id == 2
    assert created == ["dir_a", "dir_b"]
