"""过小或没有视频流的文件停在失败，不再进入上传队列。"""

from pathlib import Path
from types import SimpleNamespace

from src.pipeline.ingest.preview import PreviewJob, PreviewPool


class _Hub:
    def __init__(self, tmp_path: Path) -> None:
        self._settings = SimpleNamespace(page_dir=tmp_path / "page")

    def get(self):
        return self._settings


class _Repo:
    def __init__(self) -> None:
        self.failed: tuple[int, int, int, str] | None = None
        self.preview_calls = 0

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

    async def update_preview(self, task_id: int, page_path, success: bool, error_message: str = "") -> None:
        del task_id, page_path, success, error_message
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
