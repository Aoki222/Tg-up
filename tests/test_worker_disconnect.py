"""短暂停线时任务留在原账号，进度不回到 0；多次重连失败才回等待队列。"""

from pathlib import Path
from types import SimpleNamespace

from src.domain.task import AfterSuccess, Task, TaskArtifacts, TaskDestination, TaskPolicy, TaskStatus
from src.pipeline.worker import UploadWorker
from src.ports.transport import SendDisconnected, SendOk


def _task(path: Path) -> Task:
    return Task(
        id=7,
        file_path=str(path),
        file_name=path.name,
        file_size=1_000_000,
        destination=TaskDestination(chat_id=-100, dest_id="-100", platform="telegram"),
        artifacts=TaskArtifacts(video_path=str(path)),
        policy=TaskPolicy(need_preview=False, after_success=AfterSuccess.KEEP, max_retries=3),
        status=TaskStatus.ASSIGNED,
    )


class _Hub:
    def get(self):
        return SimpleNamespace(concurrency=1, upload_timeout_seconds=30)


class _Repo:
    def __init__(self) -> None:
        self.released: list[tuple[int, str]] = []
        self.succeeded: list[tuple[int, int]] = []

    async def get_task_by_id(self, task_id: int):
        return None

    async def mark_task_uploading(self, task_id: int) -> None:
        return None

    async def mark_task_succeeded(self, task_id: int, message_id: int) -> None:
        self.succeeded.append((task_id, message_id))

    async def release_task(self, task_id: int, error_message: str) -> None:
        self.released.append((task_id, error_message))


class _After:
    async def handle(self, task: Task) -> None:
        return None


class _Progress:
    def __init__(self) -> None:
        self.events = []

    def report(self, progress) -> None:
        self.events.append(progress)


class _Client:
    def __init__(self, pool: "_Pool") -> None:
        self.pool = pool

    def is_connected(self) -> bool:
        return self.pool.connected


class _Pool:
    def __init__(self, connected: bool) -> None:
        self.connected = connected
        self.marked: list[str] = []
        self.clients = {"Homa": _Client(self)}
        self.reconnect: dict = {}

    def is_reconnecting(self, name: str) -> bool:
        return False

    def can_retry_now(self, name: str) -> bool:
        return True

    def mark_disconnected(self, name: str, reason: str) -> None:
        self.marked.append(reason)

    async def ensure_client(self, name: str, path: Path):
        return self.clients[name] if self.connected else None


class _Transport:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = 0
        self.invalidated = 0

    async def send(self, task, timeout_seconds: int, on_progress=None):
        self.calls += 1
        if self.calls == 1 and on_progress is not None:
            on_progress(500_000, 1_000_000)
        return self.results.pop(0)

    async def invalidate_pool(self) -> None:
        self.invalidated += 1


async def _noop_sleep(_seconds: float) -> None:
    return None


async def _drive(worker: UploadWorker, task: Task) -> None:
    await worker.task_queue.put(task)
    await worker.task_queue.get()
    await worker.process_single_task(task)


async def test_disconnect_stays_on_same_account_and_keeps_bytes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.worker.asyncio.sleep", _noop_sleep)
    repo = _Repo()
    progress = _Progress()
    transport = _Transport([SendDisconnected("reset"), SendOk(11)])
    pool = _Pool(connected=True)
    worker = UploadWorker(
        "Homa",
        repo,
        transport,
        _After(),
        _Hub(),
        progress_reporter=progress,
        session_pool=pool,
        session_path=tmp_path / "Homa",
        account_kind="bot",
    )
    await _drive(worker, _task(tmp_path / "clip.mp4"))
    assert transport.calls == 2
    assert transport.invalidated == 0
    assert pool.marked == []
    assert repo.released == []
    assert repo.succeeded == [(7, 11)]
    reconnecting = [item for item in progress.events if item.message == "正在重连"]
    assert len(reconnecting) == 1
    assert reconnecting[0].current == 500_000
    assert reconnecting[0].total == 1_000_000
    assert reconnecting[0].stage == "uploading"
    assert all(not (item.total == 1 and item.stage == "flood_wait") for item in progress.events)


async def test_repeated_disconnect_returns_task_to_queue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.worker.asyncio.sleep", _noop_sleep)
    repo = _Repo()
    progress = _Progress()
    transport = _Transport([SendDisconnected("reset")])
    pool = _Pool(connected=False)
    worker = UploadWorker(
        "Homa",
        repo,
        transport,
        _After(),
        _Hub(),
        progress_reporter=progress,
        session_pool=pool,
        session_path=tmp_path / "Homa",
        account_kind="bot",
    )
    await _drive(worker, _task(tmp_path / "clip.mp4"))
    assert transport.calls == 1
    assert transport.invalidated == 1
    assert pool.marked == ["reset"]
    assert repo.released == [(7, "disconnected: reset")]
    assert repo.succeeded == []
    gave_up = [item for item in progress.events if item.stage == "flood_wait"]
    assert len(gave_up) == 1
    assert gave_up[0].current == 500_000
    assert gave_up[0].total == 1_000_000
    assert gave_up[0].message == "重连失败，已回队列"
