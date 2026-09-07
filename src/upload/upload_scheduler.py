import asyncio
from ..logger import get_logger
from .task_repository import TaskRepository

logger = get_logger(__name__)

class UploadScheduler:
    """职责：DB pending 任务 -> 原子抢占 -> 分给有空槽的 Worker。不自建 Watcher。"""

    def __init__(self, task_repository: TaskRepository, max_tasks_per_worker: int = 3, poll_interval_seconds: int = 5):
        self.wakeup_event = asyncio.Event()
        self.task_repository = task_repository
        self.max_tasks_per_worker = max_tasks_per_worker
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_map: dict = {}
        self.is_running = True
        self.stop_event = asyncio.Event()
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
            timeout_task = getattr(self, "timeout_task", None)
            if timeout_task is not None:
                timeout_task.cancel()
                 
    async def stop(self) -> None:
        self.is_running = False
        self.request_reschedule()
        timeout_task = getattr(self, "timeout_task", None)
        if timeout_task is not None:
            timeout_task.cancel()
            try:
                await timeout_task
            except (asyncio.CancelledError, Exception):
                pass

    async def schedule_once(self) -> None:
        available_workers = []
        for worker_name in self.worker_map:
            active = await self.task_repository.count_active_tasks(worker_name)
            if active < self.max_tasks_per_worker:
                available_workers.append((worker_name, self.max_tasks_per_worker - active))
        if not available_workers:
            return
        for worker_name, free_slots in available_workers:
            for task in await self.task_repository.fetch_pending_tasks(free_slots):
                if await self.task_repository.claim_task(task["id"], worker_name):
                    await self.worker_map[worker_name].enqueue_task(dict(task))

    async def check_timeout_loop(self) -> None:
        try:
            while self.is_running:
                await asyncio.sleep(60)
                recovered = await self.task_repository.recover_timed_out_tasks()
                if recovered:
                    logger.warning("已回收超时任务: %s", recovered)
                self.request_reschedule()
        except asyncio.CancelledError:
            pass