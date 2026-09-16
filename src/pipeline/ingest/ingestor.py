"""发现层之后的入库。

顺序：扩展名过滤 → 路径去重 → 等文件写完 → 再建话题 → 入库。
写稳和建话题不占全局锁，多个文件可并行等待；只有「再查重 + INSERT」串行。
需要封面时写成 preparing 后立刻返回；截图由 PreviewPool 做完再转 pending。
封面失败不丢视频：page_path 置空，仍然上传。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ...adapters.task_store import TaskRepository
from ...domain.settings_hub import SettingsHub
from ...domain.task import TaskStatus
from ...logger import get_logger
from ...ports.rescheduler import Rescheduler
from ...utils.topic_creactor import TopicCreator
from .policy import IngestPolicy
from .preview import PreviewJob, PreviewPool

logger = get_logger(__name__)

# 同时等写稳 / 建话题的文件数。INSERT 仍由 _insert_lock 串行。
INGEST_CONCURRENCY = 8


async def wait_until_file_stable(
    file_path: Path,
    check_interval: float = 2.0,
    stable_rounds: int = 3,
    timeout_seconds: float = 1800,
) -> int | None:
    """等拷贝/下载结束：连续若干次看到的文件大小不变才返回。

    第一次 stat 算一轮。mtime 已经久于「还要再观察的总间隔」时，
    视为启动扫盘碰到的旧文件，立刻返回，不必再睡。
    文件中途消失或总等待超时返回 None，本轮放弃，等下次扫盘或新事件再试。
    """
    deadline = time.monotonic() + timeout_seconds
    try:
        stat_result = file_path.stat()
    except OSError:
        return None
    if not file_path.is_file():
        return None

    last_size = stat_result.st_size
    age = time.time() - stat_result.st_mtime
    remaining_rounds = max(0, stable_rounds - 1)
    if remaining_rounds == 0 or age >= check_interval * remaining_rounds:
        return last_size

    stable_hits = 1
    while stable_hits < stable_rounds:
        if time.monotonic() >= deadline:
            logger.warning("等待文件稳定超时: %s", file_path)
            return None
        await asyncio.sleep(check_interval)
        if not file_path.is_file():
            return None
        current_size = file_path.stat().st_size
        if current_size == last_size:
            stable_hits += 1
        else:
            stable_hits = 1
            last_size = current_size
    return last_size


class FileIngestor:
    """发现层之后：写稳、按当前配置拍策略快照、入库。"""

    def __init__(
        self,
        task_repository: TaskRepository,
        rescheduler: Rescheduler,
        settings_hub: SettingsHub,
        topic_creator: TopicCreator | None = None,
        preview_pool: PreviewPool | None = None,
        concurrency: int = INGEST_CONCURRENCY,
    ):
        self.task_repository = task_repository
        self.rescheduler = rescheduler
        self.settings_hub = settings_hub
        self.topic_creator = topic_creator
        self.preview_pool = preview_pool
        self.ingest_policy = IngestPolicy()
        self.concurrency = max(1, concurrency)
        self._gate = asyncio.Semaphore(self.concurrency)
        self._insert_lock = asyncio.Lock()
        self._paths_lock = asyncio.Lock()
        self._inflight_paths: set[str] = set()
        self._running = True
        self._file_queue: asyncio.Queue[Path | None] | None = None
        self._inflight: set[asyncio.Task] = set()

    async def handle_new_file(self, file_path: Path, caption: str = "") -> int | None:
        """发现层回调。同一路径正在处理时直接跳过，避免并行双等。

        返回入库后的数字 id；扩展名不符、未写稳则 None。
        已有未完成任务时返回那条 id，不重复插入。
        """
        file_path = file_path.resolve()
        settings = self.settings_hub.get()
        decision = self.ingest_policy.decide(file_path, settings)
        if not decision.allowed:
            logger.info("跳过非目标文件: %s", file_path)
            return None

        path_key = str(file_path)
        async with self._paths_lock:
            if path_key in self._inflight_paths:
                logger.info("已在入库中，忽略重复发现: %s", file_path)
                return None
            self._inflight_paths.add(path_key)
        try:
            existing = await self.task_repository.find_active_by_file_path(path_key)
            if existing:
                logger.info("已有未完成任务，忽略重复发现: %s", file_path)
                return existing
            async with self._gate:
                return await self._ingest_claimed(file_path, caption)
        finally:
            async with self._paths_lock:
                self._inflight_paths.discard(path_key)

    async def _ingest_claimed(self, file_path: Path, caption: str) -> int | None:
        settings = self.settings_hub.get()
        file_size = await wait_until_file_stable(
            file_path,
            timeout_seconds=settings.stable_timeout_seconds,
        )
        if file_size is None:
            logger.warning("文件不存在或未写稳: %s", file_path)
            return None
        if not self._running:
            return None

        # 写稳可能很久，热更新后的扩展名 / 封面 / 群要重新决议
        settings = self.settings_hub.get()
        decision = self.ingest_policy.decide(file_path, settings)
        if not decision.allowed:
            logger.info("跳过非目标文件: %s", file_path)
            return None

        file_name = file_path.name
        folder_name = file_path.parent.name
        folder_path = str(file_path.parent.resolve())
        need_preview = decision.need_single or decision.need_content
        policy = self.settings_hub.policy_for_new_task(need_preview)

        topic_id = None
        if settings.topic_creation_enabled and self.topic_creator is not None:
            topic_id = await self.topic_creator.get_or_create_topic(
                fold_path=folder_path,
                folder_name=folder_name,
                chat_id=decision.chat_id,
            )

        if not self._running:
            return None

        async with self._insert_lock:
            existing = await self.task_repository.find_active_by_file_path(str(file_path))
            if existing:
                logger.info("已有未完成任务，忽略重复发现: %s", file_path)
                return existing
            status = TaskStatus.PENDING if not need_preview else TaskStatus.PREPARING
            task_id = await self.task_repository.add_task(
                file_path=str(file_path),
                file_name=file_name,
                file_size=file_size,
                folder_name=folder_name,
                single_page=decision.need_single,
                content_page=decision.need_content,
                chat_id=decision.chat_id,
                topic_id=topic_id,
                status=status.value,
                max_retries=policy.max_retries,
                caption=caption,
                after_success=policy.after_success.value,
            )
            self.settings_hub.remember_policy(task_id, policy)

        logger.info("已入库 task=%s: %s", task_id, file_path)

        if not need_preview:
            self.rescheduler.request_reschedule()
            return task_id

        if self.preview_pool is None:
            await self.task_repository.update_preview(task_id, None, False, "preview pool missing")
            self.rescheduler.request_reschedule()
            return task_id
        self.preview_pool.submit(
            PreviewJob(
                task_id=task_id,
                file_path=file_path,
                need_single=decision.need_single,
                need_content=decision.need_content,
            )
        )
        return task_id

    async def consume(self, file_queue: asyncio.Queue[Path | None]) -> None:
        """并行消费发现队列。单条失败只记日志；None 或 stop() 后退出。"""
        self._file_queue = file_queue
        try:
            while self._running:
                file_path = await file_queue.get()
                if file_path is None:
                    file_queue.task_done()
                    break
                task = asyncio.create_task(self._run_guarded(file_path))
                self._inflight.add(task)
        except asyncio.CancelledError:
            self._running = False
            for task in list(self._inflight):
                task.cancel()
            raise
        finally:
            pending = list(self._inflight)
            if pending:
                await asyncio.wait(pending)

    async def _run_guarded(self, file_path: Path) -> None:
        try:
            await self.handle_new_file(file_path)
        except asyncio.CancelledError:
            if not self._running:
                return
            raise
        except Exception:
            logger.exception("处理发现文件失败: %s", file_path)
        finally:
            current = asyncio.current_task()
            if current is not None:
                self._inflight.discard(current)
            if self._file_queue is not None:
                self._file_queue.task_done()

    async def stop(self) -> None:
        """不再接新文件，并取消正在跑的入库（含写稳等待）。"""
        self._running = False
        for task in list(self._inflight):
            task.cancel()
        if self._file_queue is not None:
            try:
                self._file_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        pending = list(self._inflight)
        if pending:
            await asyncio.wait(pending, timeout=3)
