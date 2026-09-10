import asyncio

from ..domain.settings_hub import SettingsHub
from ..domain.task import task_from_row
from ..logger import get_logger
from .task_repository import TaskRepository

logger = get_logger(__name__)


class UploadScheduler:
    """DB pending -> 原子抢占 -> 有空槽且未在 FloodWait 的 Worker。"""

    def __init__(
        self,
        task_repository: TaskRepository,
        settings_hub: SettingsHub,
        poll_interval_seconds: int = 5,
    ):
        self.wakeup_event = asyncio.Event()
        self.task_repository = task_repository
        self.settings_hub = settings_hub
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_map: dict = {}
        self.is_running = True
        self.timeout_task = None

    def request_reschedule(self) -> None:
        self.wakeup_event.set()

    def register_worker(self, worker) -> None:
        self.worker_map[worker.worker_name] = worker

    async def run_forever(self) -> None:
        self.request_reschedule()
        self.timeout_task = asyncio.create_task(self.check_timeout_loop())
        try:
            while self.is_running:
                try:
                    await asyncio.wait_for(self.wakeup_event.wait(), timeout=self.poll_interval_seconds)
                except TimeoutError:
                    pass
                self.wakeup_event.clear()
                try:
                    await self.schedule_once()
                except Exception as error:
                    logger.exception("调度异常: %s", error)
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            timeout_task = self.timeout_task
            if timeout_task is not None:
                timeout_task.cancel()

    async def stop(self) -> None:
        self.is_running = False
        self.request_reschedule()
        timeout_task = self.timeout_task
        if timeout_task is not None:
            timeout_task.cancel()
            try:
                await timeout_task
            except (asyncio.CancelledError, Exception):
                pass

    async def schedule_once(self) -> None:
        settings = self.settings_hub.get()
        available_workers = []
        for worker_name, worker in self.worker_map.items():
            if not worker.is_accepting():
                continue
            active = await self.task_repository.count_active_tasks(worker_name)
            if active < settings.concurrency:
                available_workers.append((worker_name, settings.concurrency - active))
        if not available_workers:
            return
        for worker_name, free_slots in available_workers:
            for row in await self.task_repository.fetch_pending_tasks(free_slots):
                if await self.task_repository.claim_task(row["id"], worker_name):
                    policy = self.settings_hub.policy_for_row(row)
                    await self.worker_map[worker_name].enqueue_task(task_from_row(row, policy))

    async def check_timeout_loop(self) -> None:
        try:
            while self.is_running:
                await asyncio.sleep(60)
                settings = self.settings_hub.get()
                recovered = await self.task_repository.recover_timed_out_tasks(
                    uploading_timeout_seconds=settings.upload_timeout_seconds,
                    assigned_timeout_seconds=settings.assigned_timeout_seconds,
                )
                if recovered:
                    logger.warning("已回收超时任务: %s", recovered)
                self.request_reschedule()
        except asyncio.CancelledError:
            pass
