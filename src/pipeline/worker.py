"""单个 Telegram 账号的上传执行器。

调度器只负责把任务塞进本 worker 的内存队列。这里负责：
- 用信号量限制本账号并发（上限跟 upload.toml 的 concurrency 走）
- 调 Transport 发送
- FloodWait：任务回 pending、不增加失败次数，本 worker 暂停接新活
- 普通失败：retry_count+1，超限才 failed
- 成功后按任务自己的 policy 做本地收尾（删/留/归档）

serve_forever 只分发、不等上传结束；Queue.join() 等的是 process_single_task 里的 task_done。
"""

import asyncio
import time
from typing import Callable

from ..adapters.task_store import TaskRepository
from ..domain.concurrency import ConcurrencyGate
from ..domain.settings_hub import SettingsHub
from ..domain.task import Task
from ..logger import get_logger
from ..ports.after_upload import AfterUpload
from ..ports.transport import SendFailed, SendOk, SendRetryLater, Transport

logger = get_logger(__name__)


class UploadWorker:
    """从内存队列取 Task，交给 Transport；成功后走 AfterUpload。"""

    def __init__(
        self,
        worker_name: str,
        task_repository: TaskRepository,
        transport: Transport,
        after_upload: AfterUpload,
        settings_hub: SettingsHub,
        on_task_finished: Callable[[], None] | None = None,
    ):
        self.worker_name = worker_name
        self.task_repository = task_repository
        self.transport = transport
        self.after_upload = after_upload
        self.settings_hub = settings_hub
        self.on_task_finished = on_task_finished
        self.concurrency_gate = ConcurrencyGate(lambda: self.settings_hub.get().concurrency)
        self.task_queue: asyncio.Queue[Task | None] = asyncio.Queue()
        self.background_tasks: set[asyncio.Task] = set()
        self.is_running = True
        self.flood_wait_until = 0.0

    def is_accepting(self) -> bool:
        return self.is_running and time.monotonic() >= self.flood_wait_until

    async def enqueue_task(self, task: Task) -> None:
        await self.task_queue.put(task)
        logger.info("[%s] 任务已加入队列: %s", self.worker_name, task.file_name)

    async def serve_forever(self) -> None:
        """一直从队列取任务并后台执行。None 是停止哨兵。"""
        while self.is_running:
            try:
                await self._wait_if_flooded()
                task = await self.task_queue.get()
            except asyncio.CancelledError:
                break
            if task is None:
                self.task_queue.task_done()
                break
            # 只分发不等待：真正完成时 process_single_task 里 task_done，join() 才准
            child_task = asyncio.create_task(self.process_single_task(task))
            self.background_tasks.add(child_task)
            child_task.add_done_callback(self.background_tasks.discard)

    async def stop(self, drain_timeout_seconds: float = 60.0) -> None:
        """不再取新任务。队列里剩下的尽量做完，超时则取消在途协程。

        若 serve_forever 已被 TaskGroup 取消，哨兵可能没人消费，
        下面会自己把哨兵 task_done 掉，避免 join() 永远等。
        """
        self.is_running = False
        try:
            self.task_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        await asyncio.sleep(0.1)
        leftover_tasks: list[Task] = []
        while True:
            try:
                queued_item = self.task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if queued_item is None:
                self.task_queue.task_done()
                continue
            leftover_tasks.append(queued_item)
        for leftover in leftover_tasks:
            child_task = asyncio.create_task(self.process_single_task(leftover))
            self.background_tasks.add(child_task)
            child_task.add_done_callback(self.background_tasks.discard)
        try:
            await asyncio.wait_for(self.task_queue.join(), timeout=drain_timeout_seconds)
        except TimeoutError:
            logger.warning("[%s] 排空队列超时，剩余在途任务数=%s", self.worker_name, len(self.background_tasks))
        if self.background_tasks:
            _, pending = await asyncio.wait(self.background_tasks, timeout=drain_timeout_seconds)
            for unfinished in pending:
                unfinished.cancel()
            if pending:
                logger.warning("[%s] %s 个上传任务未做完已取消", self.worker_name, len(pending))

    async def process_single_task(self, task: Task) -> None:
        """处理一条任务。无论成败，finally 里都要 task_done 并唤醒调度器补位。"""
        try:
            await self._wait_if_flooded()
            # 开工重读：拿到入库后才回填的 page_path，policy 仍用队列里那份快照
            fresh = await self.task_repository.get_task_by_id(task.id)
            if fresh:
                task = task.merge_row(fresh)
            settings = self.settings_hub.get()
            async with self.concurrency_gate:
                await self.task_repository.mark_task_uploading(task.id)
                result = await self.transport.send(task, settings.upload_timeout_seconds)
                if isinstance(result, SendOk):
                    await self.task_repository.mark_task_succeeded(task.id, result.message_id)
                    try:
                        await self.after_upload.handle(task)
                    except Exception:
                        logger.exception("[%s] 上传后收尾失败: %s", self.worker_name, task.file_path)
                    logger.info("[%s] 上传成功: %s", self.worker_name, task.file_path)
                elif isinstance(result, SendRetryLater):
                    self.flood_wait_until = time.monotonic() + result.seconds
                    # 回 pending 且不 +retry_count；调度器会跳过 is_accepting()==False 的 worker
                    await self.task_repository.release_task(task.id, f"FloodWait {result.seconds}s")
                    logger.warning(
                        "[%s] FloodWait %ss，任务回队列且不计失败: %s",
                        self.worker_name,
                        result.seconds,
                        task.file_path,
                    )
                elif isinstance(result, SendFailed):
                    logger.error("[%s] 上传失败: %s (%s)", self.worker_name, task.file_path, result.reason)
                    await self._handle_upload_failure(task, result.reason)
        except Exception as error:
            logger.exception("[%s] 上传失败: %s", self.worker_name, task.file_path)
            await self._handle_upload_failure(task, str(error))
        finally:
            self.task_queue.task_done()
            if self.on_task_finished is not None:
                try:
                    self.on_task_finished()
                except Exception:
                    logger.exception("[%s] 唤醒调度器失败", self.worker_name)

    async def _wait_if_flooded(self) -> None:
        remaining = self.flood_wait_until - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _handle_upload_failure(self, task: Task, error_message: str) -> None:
        next_retry = task.retry_count + 1
        await self.task_repository.mark_task_failed(
            task.id,
            next_retry,
            task.policy.max_retries,
            error_message,
        )
