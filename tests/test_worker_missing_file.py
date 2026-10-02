"""源文件已不在，或仍是下载分轨时，一次记 failed，不再占着账号回队列。"""

from pathlib import Path
from types import SimpleNamespace

from src.domain.task import AfterSuccess, Task, TaskArtifacts, TaskDestination, TaskPolicy, TaskStatus
from src.pipeline.worker import UploadWorker
from src.ports.transport import SendFailed


def _task(path: Path, *, retry_count: int = 0) -> Task:
    return Task(
        id=7,
        file_path=str(path),
        file_name=path.name,
        file_size=8,
        destination=TaskDestination(chat_id=-100, dest_id="-100", platform="telegram"),
        artifacts=TaskArtifacts(video_path=str(path)),
        policy=TaskPolicy(need_preview=False, after_success=AfterSuccess.KEEP, max_retries=3),
        status=TaskStatus.ASSIGNED,
        retry_count=retry_count,
    )


class _Hub:
    def get(self):
        return SimpleNamespace(concurrency=1, upload_timeout_seconds=30)


class _Repo:
    def __init__(self) -> None:
        self.failed: tuple[int, int, int, str] | None = None
        self.uploads = 0

    async def get_task_by_id(self, task_id: int):
        return None

    async def mark_task_uploading(self, task_id: int) -> None:
        self.uploads += 1

    async def mark_task_failed(self, task_id: int, retry_count: int, max_retries: int, error_message: str) -> None:
        self.failed = (task_id, retry_count, max_retries, error_message)


class _Transport:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0

    async def send(self, task, timeout_seconds: int, on_progress=None):
        self.calls += 1
        return self.result


async def _drive(worker: UploadWorker, task: Task) -> None:
    await worker.task_queue.put(task)
    await worker.task_queue.get()
    await worker.process_single_task(task)


async def test_missing_file_fails_without_another_retry(tmp_path: Path) -> None:
    repo = _Repo()
    transport = _Transport(SendFailed("文件不存在: /downloads/gone.webm", retryable=False))
    worker = UploadWorker("Homa", repo, transport, SimpleNamespace(), _Hub(), account_kind="bot")
    await _drive(worker, _task(tmp_path / "gone.webm"))
    assert transport.calls == 1
    assert repo.uploads == 1
    assert repo.failed == (7, 3, 3, "文件不存在: /downloads/gone.webm")


async def test_ordinary_failure_still_counts_one_retry(tmp_path: Path) -> None:
    repo = _Repo()
    transport = _Transport(SendFailed("boom"))
    worker = UploadWorker("Homa", repo, transport, SimpleNamespace(), _Hub(), account_kind="bot")
    await _drive(worker, _task(tmp_path / "觉醒.webm", retry_count=1))
    assert repo.failed == (7, 2, 3, "boom")


async def test_queued_dash_split_is_not_uploaded(tmp_path: Path) -> None:
    repo = _Repo()
    transport = _Transport(SendFailed("should-not-send"))
    worker = UploadWorker("Homa", repo, transport, SimpleNamespace(), _Hub(), account_kind="bot")
    await _drive(worker, _task(tmp_path / "养眼身材.f251-1.webm"))
    assert transport.calls == 0
    assert repo.uploads == 0
    assert repo.failed is not None
    task_id, retry_count, max_retries, reason = repo.failed
    assert task_id == 7
    assert retry_count == max_retries == 3
    assert "f251-1" in reason
