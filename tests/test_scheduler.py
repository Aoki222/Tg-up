import asyncio
from pathlib import Path

from src.domain.task import AfterSuccess
from src.domain.upload_settings import PreviewMode, UploadSettings
from src.pipeline.schedule.scheduler import UploadScheduler, pick_least_loaded


class _Hub:
    def __init__(self, concurrency: int = 3):
        self._settings = UploadSettings(
            chat_id=1,
            observer_paths=(Path("d"),),
            page_dir=Path("p"),
            archive_dir=Path("a"),
            preview=PreviewMode.OFF,
            topic_creation_enabled=False,
            after_success=AfterSuccess.KEEP,
            concurrency=concurrency,
            max_retries=3,
            upload_timeout_seconds=1,
            assigned_timeout_seconds=1,
            stable_timeout_seconds=1,
            watch_extensions=frozenset(),
        )

    def get(self) -> UploadSettings:
        return self._settings

    def policy_for_row(self, row: dict):
        raise AssertionError("should not claim when slots are computed from DB only")


class _Worker:
    def __init__(self, name: str):
        self.worker_name = name
        self.task_queue: asyncio.Queue = asyncio.Queue()

    def is_accepting(self) -> bool:
        return True


class _Repo:
    def __init__(self, counts: dict[str, int]):
        self.counts = counts
        self.fetch_limit: int | None = None

    async def count_active_by_workers(self) -> dict[str, int]:
        return self.counts

    async def fetch_pending_tasks(self, limit: int) -> list[dict]:
        self.fetch_limit = limit
        return []


def test_round_robin_when_all_idle() -> None:
    loads = {"A": 0, "B": 0, "C": 0}
    names = []
    rr = 0
    for _ in range(6):
        name, rr = pick_least_loaded(loads, concurrency=3, rr=rr)
        assert name is not None
        names.append(name)
        loads[name] += 1
        # 模拟传得很快，立刻空闲
        loads[name] -= 1
    assert names == ["A", "B", "C", "A", "B", "C"]


def test_prefers_least_loaded() -> None:
    loads = {"A": 2, "B": 0, "C": 1}
    name, _rr = pick_least_loaded(loads, concurrency=3, rr=0)
    assert name == "B"


def test_skips_full_workers() -> None:
    loads = {"A": 3, "B": 3, "C": 1}
    name, _rr = pick_least_loaded(loads, concurrency=3, rr=0)
    assert name == "C"
    name, _rr = pick_least_loaded({"A": 3, "B": 3, "C": 3}, concurrency=3, rr=0)
    assert name is None


async def test_load_ignores_memory_queue_size() -> None:
    """DB 里已有 2 条 assigned，队列里又是那 2 条时，仍剩 1 个空槽。"""
    repo = _Repo({"A": 2})
    worker = _Worker("A")
    await worker.task_queue.put(object())
    await worker.task_queue.put(object())
    scheduler = UploadScheduler(repo, _Hub(concurrency=3))
    scheduler.register_worker(worker)
    await scheduler.schedule_once()
    assert repo.fetch_limit == 1


async def test_coalesce_wakeups_into_one_schedule() -> None:
    scheduler = UploadScheduler(_Repo({}), _Hub(), poll_interval_seconds=2, coalesce_seconds=0.05)
    calls: list[int] = []

    async def fake_once() -> None:
        calls.append(1)

    scheduler.schedule_once = fake_once  # type: ignore[method-assign]
    task = asyncio.create_task(scheduler.run_forever())
    try:
        await asyncio.sleep(0.08)
        assert len(calls) == 1
        for _ in range(8):
            scheduler.request_reschedule()
        await asyncio.sleep(0.12)
        assert len(calls) == 2
    finally:
        scheduler.is_running = False
        scheduler.request_reschedule()
        await asyncio.wait_for(task, timeout=1)
