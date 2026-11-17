"""过小或没有视频流的文件停在失败，不再进入上传队列。"""

from pathlib import Path
from types import SimpleNamespace

from src.adapters.task_store import TaskRepository
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import SCHEMA
from src.pipeline.ingest.preview import PreviewJob, PreviewPool
from src.utils.video_preview import NoVideoStreamError


class _Hub:
    def __init__(self, tmp_path: Path) -> None:
        self._settings = SimpleNamespace(page_dir=tmp_path / "page")

    def get(self):
        return self._settings


class _Repo:
    def __init__(self) -> None:
        self.failed: tuple[int, int, int, str] | None = None
        self.preview_calls = 0
        self.last_actual_size: int | None = None

    async def get_task_by_id(self, task_id: int) -> dict:
        return {"id": task_id, "max_retries": 3, "status": "preparing"}

    async def mark_task_failed(
        self,
        task_id: int,
        retry_count: int,
        max_retries: int,
        error_message: str,
        claim_token: str | None = None,
    ) -> bool:
        del claim_token
        self.failed = (task_id, retry_count, max_retries, error_message)
        return True

    async def update_preview(
        self,
        task_id: int,
        page_path,
        success: bool,
        error_message: str = "",
        actual_size: int | None = None,
    ) -> None:
        del task_id, page_path, success, error_message
        self.last_actual_size = actual_size
        self.preview_calls += 1


class _Scheduler:
    def __init__(self) -> None:
        self.calls = 0

    def request_reschedule(self) -> None:
        self.calls += 1


async def test_tiny_preview_is_rejected_without_upload(tmp_path: Path) -> None:
    stub = tmp_path / "stub.mp4"
    stub.write_bytes(b"x" * 262)
    repo = _Repo()
    scheduler = _Scheduler()
    pool = PreviewPool(repo, scheduler, _Hub(tmp_path))
    job = PreviewJob(task_id=9, file_path=stub, need_single=False, need_content=True)
    pool.queue.put_nowait(job)
    await pool.queue.get()
    await pool._run_guarded(job)
    assert repo.preview_calls == 0
    assert repo.failed is not None
    assert repo.failed[0] == 9
    assert repo.failed[1] == repo.failed[2] == 3
    assert "过小" in repo.failed[3]
    assert scheduler.calls == 1


async def test_preview_retries_on_transient_no_video_stream(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "race.mp4"
    video.write_bytes(b"v" * 8192)
    repo = _Repo()
    scheduler = _Scheduler()
    pool = PreviewPool(
        repo,
        scheduler,
        _Hub(tmp_path),
        video_stream_retries=3,
        retry_delays=(0.01, 0.02),
    )
    job = PreviewJob(task_id=12, file_path=video, need_single=True, need_content=False)

    calls = {"count": 0}

    async def mock_first_frame(self, path, _):
        calls["count"] += 1
        if calls["count"] == 1:
            raise NoVideoStreamError(f"媒体文件中无可用视频流: {path.name}")
        return str(tmp_path / "page.jpg")

    async def mock_ensure_thumb(self, path, page):
        return None

    monkeypatch.setattr(
        "src.utils.video_preview.FirstFramePreview.extract_first_frame_async",
        mock_first_frame,
    )
    monkeypatch.setattr(
        "src.utils.video_preview.VideoThumbnail.ensure_thumbnail_async",
        mock_ensure_thumb,
    )

    pool.queue.put_nowait(job)
    await pool.queue.get()
    await pool._run_guarded(job)

    assert calls["count"] == 2
    assert repo.failed is None
    assert repo.preview_calls == 1
    assert repo.last_actual_size == 8192
    assert scheduler.calls == 1


async def test_preview_exhausts_retries_and_rejects(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"b" * 8192)
    repo = _Repo()
    scheduler = _Scheduler()
    pool = PreviewPool(
        repo,
        scheduler,
        _Hub(tmp_path),
        video_stream_retries=3,
        retry_delays=(0.01, 0.02),
    )
    job = PreviewJob(task_id=15, file_path=video, need_single=True, need_content=False)

    calls = {"count": 0}

    async def mock_first_frame(self, path, _):
        calls["count"] += 1
        raise NoVideoStreamError(f"媒体文件中无可用视频流: {path.name}")

    monkeypatch.setattr(
        "src.utils.video_preview.FirstFramePreview.extract_first_frame_async",
        mock_first_frame,
    )

    pool.queue.put_nowait(job)
    await pool.queue.get()
    await pool._run_guarded(job)

    assert calls["count"] == 3
    assert repo.preview_calls == 0
    assert repo.failed is not None
    assert repo.failed[0] == 15
    assert repo.failed[1] == repo.failed[2] == 3
    assert "无可用视频流" in repo.failed[3]
    assert scheduler.calls == 1


async def test_update_preview_updates_actual_size(tmp_path: Path) -> None:
    await open_pool(tmp_path / "app.db")
    try:
        async with get_db() as db:
            await db.executescript(SCHEMA)
            await db.commit()
        repo = TaskRepository()
        task_id = await repo.add_task(
            file_path=str(tmp_path / "video.mp4"),
            file_name="video.mp4",
            file_size=1000,
            folder_name="folder",
            chat_id=-100,
            status="preparing",
        )
        await repo.update_preview(task_id, "/path/page.jpg", True, actual_size=2048)
        row = await repo.get_task_by_id(task_id)
        assert row is not None
        assert row["file_size"] == 2048
        assert row["status"] == "pending"
        assert row["page_path"] == "/path/page.jpg"
    finally:
        await close_pool()
