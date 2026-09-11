"""调度器：只从 SQLite 里捞 pending，原子认领后再塞给 worker。

不监听目录、不上传。负载以数据库里 assigned+uploading 为准，
这样进程崩溃后启动对账，不会出现「库里占着槽、内存队列却是空的」假忙。

认领必须带 status 条件（CAS）。只 fetch 再入队会在并发下把同一行发给两个人。
FloodWait 中的 worker 通过 is_accepting() 跳过，避免限流连坐。
"""

import asyncio

from ...adapters.task_store import TaskRepository
from ...domain.settings_hub import SettingsHub
from ...domain.task import task_from_row
from ...logger import get_logger

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
        # 多次 set 可合并；万一丢掉，轮询最多隔 poll_interval 再跑
        self.wakeup_event.set()

    def register_worker(self, worker) -> None:
        self.worker_map[worker.worker_name] = worker

    def unregister_worker(self, worker_name: str) -> None:
        self.worker_map.pop(worker_name, None)

    async def run_forever(self) -> None:
        """事件唤醒 + 定时轮询。丢一次唤醒最多隔 poll_interval 再跑。"""
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
        """按每个 worker 的空槽取最小文件优先的 pending，抢到才入队。"""
        settings = self.settings_hub.get()
        available_workers = []
        for worker_name, worker in list(self.worker_map.items()):
            if not worker.is_accepting():
                continue
            active = await self.task_repository.count_active_tasks(worker_name)
            if active < settings.concurrency:
                available_workers.append((worker_name, settings.concurrency - active))
        if not available_workers:
            return
        for worker_name, free_slots in available_workers:
            worker = self.worker_map.get(worker_name)
            if worker is None:
                continue
            for row in await self.task_repository.fetch_pending_tasks(free_slots):
                # 只有 CAS 抢到 pending 才入队，防止同一任务发给两个 worker
                if await self.task_repository.claim_task(row["id"], worker_name):
                    policy = self.settings_hub.policy_for_row(row)
                    await worker.enqueue_task(task_from_row(row, policy))

    async def check_timeout_loop(self) -> None:
        """把挂太久的 assigned / uploading 打回 pending，防止槽位被幽灵任务占死。"""
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
