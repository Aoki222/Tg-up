import asyncio
import os
from typing import Callable
from telethon.errors import FloodWaitError
from ..logger import get_logger
from .task_repository import TaskRepository

logger = get_logger(__name__)

class UploadWorker:
    """职责：从内存队列取任务并上传，结束只调完成回调，不认识调度器。"""

    def __init__(self, worker_name: str, telegram_client, task_repository: TaskRepository,
                 on_task_finished: Callable[[], None] | None = None, max_concurrent_uploads: int = 3):
        self.worker_name = worker_name
        self.telegram_client = telegram_client
        self.task_repository = task_repository
        self.on_task_finished = on_task_finished
        self.concurrency_limiter = asyncio.Semaphore(max_concurrent_uploads)
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.background_tasks: set[asyncio.Task] = set()
        self.is_running = True

    async def enqueue_task(self, task: dict) -> None:
        await self.task_queue.put(task)
        logger.info("[%s] 任务已加入队列: %s", self.worker_name, task.get("file_name"))

    async def serve_forever(self) -> None:
        while self.is_running:
            try:
                task = await self.task_queue.get()
            except asyncio.CancelledError:
                break
            if task is None:  # 停止哨兵
                self.task_queue.task_done()
                break
            child_task = asyncio.create_task(self.process_single_task(task))
            self.background_tasks.add(child_task)
            child_task.add_done_callback(self.background_tasks.discard)
            # 消费循环只负责分发即返回，不在此 task_done，
            # 由 process_single_task 结束时 task_done，保证 join() 等的是真完成。

    async def stop(self, drain_timeout_seconds: float = 60.0) -> None:
        """优雅停止：不再取新任务，排空队列 + 等在途上传，超时则取消。"""
        self.is_running = False
        # 尝试唤醒还活着的 serve_forever；若它已被 TaskGroup cancel，
        # 哨兵会残留队列，下面的回收逻辑会自己收回，不会卡 join()。
        try:
            self.task_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        await asyncio.sleep(0.1)
        leftover_tasks: list[dict] = []
        while True:
            try:
                queued_item = self.task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if queued_item is None:
                self.task_queue.task_done()  # 无人消费的哨兵，自己平衡计数
                continue
            leftover_tasks.append(queued_item)  # get计数保留，由后台任务 finally 去 task_done
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

    async def process_single_task(self, task: dict) -> None:
        task_id = task["id"]
        file_path = task["file_path"]
        async with self.concurrency_limiter:
            await self.task_repository.mark_task_uploading(task_id)
            try:
                sent_message = await self.telegram_client.send_file(
                    task["chat_id"], file_path, caption=task.get("caption") or ""
                )
                await self.task_repository.mark_task_succeeded(task_id, sent_message.id)
                if os.path.exists(file_path):
                    os.remove(file_path)
                logger.info("[%s] 上传成功: %s", self.worker_name, file_path)
            except FloodWaitError as limit_error:
                await self.handle_upload_failure(task, f"FloodWait {limit_error.seconds}s")
                await asyncio.sleep(limit_error.seconds)
            except Exception as error:
                logger.exception("[%s] 上传失败: %s", self.worker_name, file_path)
                await self.handle_upload_failure(task, str(error))
            finally:
                self.task_queue.task_done()
                if self.on_task_finished is not None:
                    try:
                        self.on_task_finished()
                    except Exception:
                        logger.exception("[%s] 唤醒调度器失败", self.worker_name)

    async def handle_upload_failure(self, task: dict, error_message: str) -> None:
        next_retry = task.get("retry_count", 0) + 1
        await self.task_repository.mark_task_failed(task["id"], next_retry, task.get("max_retries", 3), error_message)