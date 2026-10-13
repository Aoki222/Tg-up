import asyncio
import os
import time
from dataclasses import replace
from pathlib import Path
from src.domain.task import AfterSuccess, TaskPolicy
from src.domain.upload_settings import FolderRoute, PreviewMode, UploadSettings
from src.pipeline.ingest.ingestor import SESSION_RETRY_SECONDS, FileIngestor, wait_until_file_stable
from src.utils.topic_creactor import TopicSessionUnavailable


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
        self.block: tuple[str, int] | None = None

    async def find_active_by_file_path(self, file_path: str) -> int | None:
        return self.active.get(file_path)

    async def find_ingest_block(
        self,
        file_path: str,
        file_size: int,
        source_mtime: float | None,
    ) -> tuple[str, int] | None:
        active_id = self.active.get(file_path)
        if active_id is not None:
            return ("active", active_id)
        return self.block

    async def record_unmatched(self, file_path: str, file_name: str, folder_name: str, file_size: int) -> None:
        return None

    async def clear_unmatched(self, file_path: str) -> None:
        return None

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
    hub = FakeHub(
        _settings(
            routes=(FolderRoute(path=tmp_path.resolve(), chat_id=-100, dest_id="-100"),),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=8)
    return ingestor, repo


def _video(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"video")
    return path


async def _size_now(file_path: Path, **_kwargs) -> int | None:
    try:
        if not file_path.is_file():
            return None
        return file_path.stat().st_size
    except OSError:
        return None


async def test_old_file_rechecks_once(tmp_path: Path) -> None:
    path = _video(tmp_path, "old.mp4")
    past = time.time() - 60
    os.utime(path, (past, past))
    started = time.monotonic()
    size = await wait_until_file_stable(path, check_interval=0.05, stable_rounds=3, timeout_seconds=2)
    elapsed = time.monotonic() - started
    assert size == 5
    assert 0.04 <= elapsed < 0.25


async def test_old_file_growth_restarts_stable_rounds(tmp_path: Path, monkeypatch) -> None:
    path = _video(tmp_path, "grow.mp4")
    past = time.time() - 60
    os.utime(path, (past, past))
    calls = {"n": 0}

    async def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            path.write_bytes(b"video!!")

    monkeypatch.setattr("src.pipeline.ingest.ingestor.asyncio.sleep", fake_sleep)
    size = await wait_until_file_stable(path, check_interval=0.05, stable_rounds=3, timeout_seconds=5)
    assert size == 7
    assert calls["n"] == 3


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


async def test_insert_lock_does_not_cover_topic_wait(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", _size_now)
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
    hub = FakeHub(
        _settings(
            topic_creation_enabled=True,
            routes=(FolderRoute(path=tmp_path.resolve(), chat_id=-100, dest_id="-100"),),
        )
    )
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


async def test_missing_session_requeues_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.ingest.ingestor.SESSION_RETRY_SECONDS", 0.01)
    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", _size_now)

    class UnavailableTopics:
        async def get_or_create_topic(self, fold_path: str, folder_name: str, chat_id: int) -> int:
            raise TopicSessionUnavailable("没有可用的 Telegram session，无法创建话题")

    repo = FakeRepo()
    hub = FakeHub(
        _settings(
            topic_creation_enabled=True,
            routes=(FolderRoute(path=tmp_path.resolve(), chat_id=-100, dest_id="-100"),),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, topic_creator=UnavailableTopics())
    path = _video(tmp_path, "wait.mp4")
    past = time.time() - 60
    os.utime(path, (past, past))
    queue: asyncio.Queue[Path | None] = asyncio.Queue()
    ingestor._file_queue = queue
    await ingestor._run_guarded(path)
    assert repo.tasks == []
    retried = await asyncio.wait_for(queue.get(), timeout=1)
    assert retried == path.resolve()
    assert SESSION_RETRY_SECONDS == 5.0


async def test_oversized_file_is_stored_without_schedule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.domain.limits.TELEGRAM_BOT_MAX_BYTES", 1)
    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", _size_now)
    ingestor, repo = _ingestor(tmp_path)
    path = _video(tmp_path, "big.mp4")
    past = time.time() - 60
    os.utime(path, (past, past))
    task_id = await ingestor.handle_new_file(path)
    assert task_id == 1
    assert repo.tasks[0]["status"] == "oversized"
    assert repo.tasks[0]["file_size"] == 5
    assert "2GB" in (repo.tasks[0]["error_msg"] or "")
    assert ingestor.rescheduler.calls == 0


async def test_oversized_preview_stays_preparing_until_cover_finishes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.domain.limits.TELEGRAM_BOT_MAX_BYTES", 1)
    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", _size_now)
    jobs: list[object] = []

    class Pool:
        def submit(self, job) -> None:
            jobs.append(job)

    repo = FakeRepo()
    hub = FakeHub(
        _settings(
            preview=PreviewMode.FIRST_FRAME,
            routes=(FolderRoute(path=tmp_path.resolve(), chat_id=-100, dest_id="-100"),),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, preview_pool=Pool(), concurrency=1)
    path = _video(tmp_path, "big.mp4")
    past = time.time() - 60
    os.utime(path, (past, past))
    task_id = await ingestor.handle_new_file(path)
    assert task_id == 1
    assert repo.tasks[0]["status"] == "preparing"
    assert ingestor.rescheduler.calls == 0
    assert len(jobs) == 1


async def test_existing_task_is_kept_when_route_no_longer_matches(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    repo = FakeRepo()
    repo.active[str(video.resolve())] = 7
    ingestor = FileIngestor(repo, FakeRescheduler(), FakeHub(_settings()), concurrency=1)
    result = await ingestor.handle_new_file(video)
    assert result == 7
    assert repo.tasks == []


async def test_blank_caption_is_rendered_from_the_route(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", _size_now)
    shown = _video(tmp_path, "Show.mp4")
    kept = _video(tmp_path, "Kept.mp4")
    past = time.time() - 60
    os.utime(shown, (past, past))
    os.utime(kept, (past, past))
    repo = FakeRepo()
    hub = FakeHub(
        _settings(
            routes=(
                FolderRoute(
                    path=tmp_path.resolve(),
                    chat_id=-100,
                    dest_id="-100",
                    caption_template="{stem}",
                ),
            ),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=1)
    await ingestor.handle_new_file(shown)
    await ingestor.handle_new_file(kept, "手工说明")
    assert repo.tasks[0]["caption"] == "Show"
    assert repo.tasks[1]["caption"] == "手工说明"


async def test_same_fingerprint_skips_insert(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", _size_now)
    ingestor, repo = _ingestor(tmp_path)
    repo.block = ("same", 3)
    assert await ingestor.handle_new_file(_video(tmp_path, "again.mp4")) == 3
    assert repo.tasks == []


async def test_manual_dispatch_ignores_fingerprint(tmp_path: Path) -> None:
    repo = FakeRepo()
    repo.block = ("same", 9)
    hub = FakeHub(
        _settings(
            observer_paths=(tmp_path,),
            routes=(FolderRoute(path=tmp_path.resolve(), chat_id=-100, dest_id="-100"),),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=1)
    result = await ingestor.manual_dispatch(_video(tmp_path, "hand.mp4"))
    assert result["ok"] is True
    assert len(repo.tasks) == 1


async def test_inner_temp_suffix_is_not_parked(tmp_path: Path) -> None:
    repo = FakeRepo()
    hub = FakeHub(
        _settings(
            routes=(
                FolderRoute(
                    path=tmp_path.resolve(),
                    chat_id=-100,
                    dest_id="-100",
                    settle_delay_seconds=180,
                ),
            ),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=1)
    path = tmp_path / "clip.tmp.mp4"
    path.write_bytes(b"video")
    assert await ingestor.handle_new_file(path) is None
    assert ingestor._settle == {}
    assert repo.tasks == []


async def test_settle_parks_outside_the_gate_and_resets_when_size_shrinks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", _size_now)
    live = tmp_path / "live"
    live.mkdir()
    repo = FakeRepo()
    hub = FakeHub(
        _settings(
            routes=(
                FolderRoute(path=tmp_path.resolve(), chat_id=-100, dest_id="-100"),
                FolderRoute(
                    path=live.resolve(),
                    chat_id=-100,
                    dest_id="-100",
                    settle_delay_seconds=180,
                ),
            ),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=1)
    try:
        parked = live / "stream.mp4"
        parked.write_bytes(b"video")
        normal = _video(tmp_path, "done.mp4")
        assert await ingestor.handle_new_file(parked) is None
        assert str(parked.resolve()) not in ingestor._inflight_paths
        assert await ingestor.handle_new_file(normal) == 1
        assert len(repo.tasks) == 1

        key = str(parked.resolve())
        item = ingestor._settle[key]
        first_ready = item.ready_at
        parked.write_bytes(b"vi")
        assert await ingestor.handle_new_file(parked) is None
        item = ingestor._settle[key]
        assert item.last_size == 2
        assert item.ready_at >= first_ready

        item.ready_at = time.monotonic() - 1
        ready = await ingestor._poll_settle_once()
        assert ready == [(key, "")]
        task_id = None
        for path_key, caption in ready:
            task_id = await ingestor.handle_new_file(Path(path_key), caption, settled=True)
        assert task_id == 2
        assert len(repo.tasks) == 2
        assert repo.tasks[1]["file_size"] == 2
        assert repo.tasks[1]["source_mtime"] == parked.stat().st_mtime
    finally:
        await ingestor.stop()


async def test_settle_releases_when_delay_becomes_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.ingest.ingestor.wait_until_file_stable", _size_now)
    repo = FakeRepo()
    route = FolderRoute(
        path=tmp_path.resolve(),
        chat_id=-100,
        dest_id="-100",
        settle_delay_seconds=180,
    )
    hub = FakeHub(_settings(routes=(route,)))
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=1)
    try:
        path = _video(tmp_path, "live.mp4")
        assert await ingestor.handle_new_file(path) is None
        hub._settings = _settings(routes=(replace(route, settle_delay_seconds=0),))
        ready = await ingestor._poll_settle_once()
        assert [item[0] for item in ready] == [str(path.resolve())]
        assert ingestor._settle == {}
    finally:
        await ingestor.stop()


async def test_settle_drops_a_missing_file(tmp_path: Path) -> None:
    repo = FakeRepo()
    hub = FakeHub(
        _settings(
            routes=(
                FolderRoute(
                    path=tmp_path.resolve(),
                    chat_id=-100,
                    dest_id="-100",
                    settle_delay_seconds=180,
                ),
            ),
        )
    )
    ingestor = FileIngestor(repo, FakeRescheduler(), hub, concurrency=1)
    try:
        path = _video(tmp_path, "gone.mp4")
        assert await ingestor.handle_new_file(path) is None
        path.unlink()
        assert await ingestor._poll_settle_once() == []
        assert ingestor._settle == {}
    finally:
        await ingestor.stop()
