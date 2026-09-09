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
        # 分发循环：只取货建后台任务，不等上传完成，靠信号量卡并发
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
        # 开工重读最新行：调度放行时图已齐，拿到回填后的 page_path
        task = await self.task_repository.get_task_by_id(task["id"]) or task
        task_id = task["id"]
        file_path = task["file_path"]
        page_path = task.get("page_path")  # 有则视频+图齐发，无则只发视频；失败回 pending 不重截
        # 并发上限 + 先落 uploading，崩了也能被超时回收捡回
        flood_wait_seconds = 0
        async with self.concurrency_limiter:
            await self.task_repository.mark_task_uploading(task_id)
            try:
                files = [file_path, page_path] if page_path and os.path.exists(page_path) else [file_path]

                sent_messages = await self.telegram_client.send_file(
                    entity=task["chat_id"],
                    file=files if len(files) > 1 else files[0],
                    # album = len(files) > 1,
                    caption=task.get("caption") or ""
                )
                # 相册回列表，单发回单条：统一取视频（首位）消息 ID
                video_message = sent_messages[0] if isinstance(sent_messages, list) else sent_messages
                await self.task_repository.mark_task_succeeded(task_id, video_message.id)
                for path in files:
                    if os.path.exists(path):
                        os.remove(path)
                logger.info("[%s] 上传成功: %s", self.worker_name, file_path)
            except FloodWaitError as limit_error:
                await self.handle_upload_failure(task, f"FloodWait {limit_error.seconds}s")
                flood_wait_seconds = limit_error.seconds
            except Exception as error:
                logger.exception("[%s] 上传失败: %s", self.worker_name, file_path)
                await self.handle_upload_failure(task, str(error))
            finally:
                # 必做：计数归还 + 唤醒调度补位，异常也不漏
                self.task_queue.task_done()
                if self.on_task_finished is not None:
                    try:
                        self.on_task_finished()
                    except Exception:
                        logger.exception("[%s] 唤醒调度器失败", self.worker_name)
        # 限流等待放信号量外，避免占着并发槽空睡
        if flood_wait_seconds:
            await asyncio.sleep(flood_wait_seconds)

    async def handle_upload_failure(self, task: dict, error_message: str) -> None:
        next_retry = task.get("retry_count", 0) + 1
        await self.task_repository.mark_task_failed(task["id"], next_retry, task.get("max_retries", 3), error_message)