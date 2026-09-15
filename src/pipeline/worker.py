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
from pathlib import Path
from typing import Callable

from ..adapters.task_store import TaskRepository
from ..domain.concurrency import ConcurrencyGate
from ..domain.progress import make_progress
from ..domain.settings_hub import SettingsHub
from ..domain.task import Task
from ..logger import get_logger
from ..ports.after_upload import AfterUpload
from ..ports.progress import ProgressReporter
from ..adapters.sessions import SessionPool
from ..ports.transport import SendDisconnected, SendFailed, SendOk, SendRetryLater, Transport

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
        progress_reporter: ProgressReporter | None = None,
        session_pool: SessionPool | None = None,
        session_path: Path | None = None,
    ):
        self.worker_name = worker_name
        self.task_repository = task_repository
        self.transport = transport
        self.after_upload = after_upload
        self.settings_hub = settings_hub
        self.on_task_finished = on_task_finished
        self.progress_reporter = progress_reporter
        self.concurrency_gate = ConcurrencyGate(lambda: self.settings_hub.get().concurrency)
        self.task_queue: asyncio.Queue[Task | None] = asyncio.Queue()
        self.background_tasks: set[asyncio.Task] = set()
        self.is_running = True
        self.flood_wait_until = 0.0
        self._aborting = False
        self._abort_reason = ""
        self.session_pool = session_pool
        self.session_path = session_path
        self._watch_task: asyncio.Task | None = None

    def is_accepting(self) -> bool:
        """调度器用：停机、FloodWait 或正在重连时不要再往这个账号塞任务。"""
        if not self.is_running or time.monotonic() < self.flood_wait_until:
            return False
        if self.session_pool is not None and self.session_pool.is_reconnecting(self.worker_name):
            return False
        return True

    async def enqueue_task(self, task: Task) -> None:
        await self.task_queue.put(task)
        logger.info("[%s] 任务已加入队列: %s", self.worker_name, task.file_name)

    async def serve_forever(self) -> None:
        """一直从队列取任务并后台执行。None 是停止哨兵。"""
        if self.session_pool is not None:
            self._watch_task = asyncio.create_task(self._watch_connection())
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

    async def abort_and_release(self, reason: str, in_flight_timeout: float = 0) -> None:
        """停接新活。队列里未开传的立刻 release；在途可短等，超时则取消后再 release。

        不把剩余任务继续 send_file。retry_count 不增加。
        """
        self._aborting = True
        self._abort_reason = reason
        self.is_running = False
        if self._watch_task is not None:
            self._watch_task.cancel()
        try:
            self.task_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        while True:
            try:
                queued_item = self.task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if queued_item is None:
                self.task_queue.task_done()
                continue
            await self.task_repository.release_task(queued_item.id, reason)
            self.task_queue.task_done()
        if not self.background_tasks:
            return
        pending = set(self.background_tasks)
        if in_flight_timeout > 0:
            _, pending = await asyncio.wait(self.background_tasks, timeout=in_flight_timeout)
        for unfinished in pending:
            unfinished.cancel()
        if self.background_tasks:
            await asyncio.wait(self.background_tasks, timeout=2.0)

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
                self._emit_progress(task, 0, max(task.file_size, 1), "uploading")
                result = await self.transport.send(
                    task,
                    settings.upload_timeout_seconds,
                    on_progress=lambda current, total: self._emit_progress(
                        task, current, total, "uploading"
                    ),
                )
                if isinstance(result, SendOk):
                    await self.task_repository.mark_task_succeeded(task.id, result.message_id)
                    self._emit_progress(task, 1, 1, "success", "上传成功")
                    try:
                        await self.after_upload.handle(task)
                    except Exception:
                        logger.exception("[%s] 上传后收尾失败: %s", self.worker_name, task.file_path)
                    logger.info("[%s] 上传成功: %s", self.worker_name, task.file_path)
                elif isinstance(result, SendDisconnected):
                    if self.session_pool is not None:
                        self.session_pool.mark_disconnected(self.worker_name, result.reason)
                    await self.task_repository.release_task(task.id, f"disconnected: {result.reason}")
                    self._emit_progress(task, 0, 1, "flood_wait", "连接断开，正在重试")
                    logger.warning("[%s] 连接断开，任务回队列: %s (%s)", self.worker_name, task.file_path, result.reason)
                elif isinstance(result, SendRetryLater):
                    self.flood_wait_until = time.monotonic() + result.seconds
                    # 回 pending 且不 +retry_count；调度器会跳过 is_accepting()==False 的 worker
                    await self.task_repository.release_task(task.id, f"FloodWait {result.seconds}s")
                    self._emit_progress(
                        task, 0, 1, "flood_wait", f"FloodWait {result.seconds}s"
                    )
                    logger.warning(
                        "[%s] FloodWait %ss，任务回队列且不计失败: %s",
                        self.worker_name,
                        result.seconds,
                        task.file_path,
                    )
                elif isinstance(result, SendFailed):
                    logger.error("[%s] 上传失败: %s (%s)", self.worker_name, task.file_path, result.reason)
                    self._emit_progress(task, 0, 1, "failed", result.reason)
                    await self._handle_upload_failure(task, result.reason)
        except asyncio.CancelledError:
            if self._aborting:
                await self.task_repository.release_task(task.id, self._abort_reason or "worker aborted")
            raise
        except Exception as error:
            logger.exception("[%s] 上传失败: %s", self.worker_name, task.file_path)
            self._emit_progress(task, 0, 1, "failed", str(error))
            await self._handle_upload_failure(task, str(error))
        finally:
            self.task_queue.task_done()
            if self.on_task_finished is not None:
                try:
                    self.on_task_finished()
                except Exception:
                    logger.exception("[%s] 唤醒调度器失败", self.worker_name)

    def _emit_progress(
        self,
        task: Task,
        current: float,
        total: float,
        stage: str,
        message: str = "",
    ) -> None:
        """同步上报。Telethon progress_callback 也可能从这里进来，不要 await。"""
        if self.progress_reporter is None:
            return
        try:
            self.progress_reporter.report(
                make_progress(
                    task_id=task.id,
                    worker_name=self.worker_name,
                    file_name=task.file_name,
                    current=current,
                    total=total,
                    stage=stage,
                    message=message,
                )
            )
        except Exception:
            logger.exception("[%s] 进度上报失败", self.worker_name)

    async def _watch_connection(self) -> None:
        """断线后按 1/2/4/8/16s（上限 30s）重试，最多 5 次，失败后再等 30s 开新一轮。"""
        pool = self.session_pool
        path = self.session_path
        if pool is None or path is None:
            return
        while self.is_running:
            try:
                client = pool.clients.get(self.worker_name)
                if client is not None and client.is_connected() and not pool.is_reconnecting(self.worker_name):
                    disconnected = getattr(client, "disconnected", None)
                    # 旧连接留下的已完成 Future 会立刻返回，不能无 sleep 地 continue
                    if disconnected is None or disconnected.done():
                        await asyncio.sleep(5)
                        continue
                    try:
                        await asyncio.wait_for(disconnected, timeout=5)
                    except TimeoutError:
                        continue
                    if not self.is_running:
                        break
                    if client.is_connected():
                        await asyncio.sleep(1)
                        continue
                    pool.mark_disconnected(self.worker_name, "Telegram 连接已断开")
                    logger.warning("[%s] Telegram 连接已断开，开始重连", self.worker_name)
                if not pool.can_retry_now(self.worker_name):
                    status = pool.reconnect.get(self.worker_name)
                    wait = 1.0
                    if status is not None:
                        wait = max(0.2, status.next_at - time.monotonic())
                    await asyncio.sleep(min(wait, 5.0))
                    continue
                restored = await pool.ensure_client(self.worker_name, path)
                if restored is not None and restored.is_connected() and self.on_task_finished is not None:
                    self.on_task_finished()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[%s] 连接守卫异常", self.worker_name)
                await asyncio.sleep(2)

    async def _wait_if_flooded(self) -> None:
        """FloodWait 未结束就睡在队列外侧，避免占着并发闸门空转。"""
        remaining = self.flood_wait_until - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _handle_upload_failure(self, task: Task, error_message: str) -> None:
        """业务失败才 +1。未超限回 pending 清空归属，超限才 failed。"""
        next_retry = task.retry_count + 1
        await self.task_repository.mark_task_failed(
            task.id,
            next_retry,
            task.policy.max_retries,
            error_message,
        )
