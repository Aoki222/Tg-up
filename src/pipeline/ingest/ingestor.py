"""发现层之后的入库。

顺序：扩展名过滤 → 路径去重 → 等文件写完 → 再建话题 → 入库。
需要封面时先写成 preparing（调度器看不见），截图完成或失败后再转 pending。
封面失败不丢视频：page_path 置空，仍然上传。

当前配置会在入库时拷进 Task.policy。之后改 upload.toml 不影响这条已入库任务。
本阶段串行消费队列，长视频截图会堵住后面的文件。
"""

import asyncio
import time
from pathlib import Path

from ...adapters.task_store import TaskRepository
from ...domain.settings_hub import SettingsHub
from ...domain.task import TaskStatus
from ...logger import get_logger
from ...ports.rescheduler import Rescheduler
from ...utils.topic_creactor import TopicCreator
from ...utils.video_preview import FirstFramePreview, GridPreview
from .policy import IngestPolicy

logger = get_logger(__name__)


async def wait_until_file_stable(
    file_path: Path,
    check_interval: float = 2.0,
    stable_rounds: int = 3,
    timeout_seconds: float = 1800,
) -> int | None:
    """等拷贝/下载结束：连续若干次看到的文件大小不变才返回。

    文件中途消失或总等待超时返回 None，本轮放弃，等下次扫盘或新事件再试。
    """
    last_size = -1
    stable_hits = 0
    deadline = time.monotonic() + timeout_seconds
    while True:
        if time.monotonic() >= deadline:
            logger.warning("等待文件稳定超时: %s", file_path)
            return None
        if not file_path.is_file():
            return None
        current_size = file_path.stat().st_size
        if current_size == last_size:
            stable_hits += 1
            if stable_hits >= stable_rounds:
                return current_size
        else:
            stable_hits = 0
            last_size = current_size
        await asyncio.sleep(check_interval)


class FileIngestor:
    """发现层之后：写稳、按当前配置拍策略快照、入库。"""

    def __init__(
        self,
        task_repository: TaskRepository,
        rescheduler: Rescheduler,
        settings_hub: SettingsHub,
        topic_creator: TopicCreator | None = None,
    ):
        self.task_repository = task_repository
        self.rescheduler = rescheduler
        self.settings_hub = settings_hub
        self.topic_creator = topic_creator
        self.ingest_policy = IngestPolicy()
        self._lock = asyncio.Lock()
        self._running = True
        self._file_queue: asyncio.Queue[Path | None] | None = None
        self._current_task: asyncio.Task | None = None

    async def handle_new_file(self, file_path: Path, caption: str = "") -> int | None:
        """发现层回调。加锁避免 watchdog 与扫盘同时处理同一路径。

        返回入库后的数字 id；扩展名不符、未写稳则 None。
        已有未完成任务时返回那条 id，不重复插入。
        """
        async with self._lock:
            return await self._handle_new_file_locked(file_path, caption)

    async def _handle_new_file_locked(self, file_path: Path, caption: str = "") -> int | None:
        settings = self.settings_hub.get()
        file_path = file_path.resolve()
        decision = self.ingest_policy.decide(file_path, settings)
        if not decision.allowed:
            logger.info("跳过非目标文件: %s", file_path)
            return None

        # watchdog 可能对同一文件打多次；未完成任务按路径去重
        existing = await self.task_repository.find_active_by_file_path(str(file_path))
        if existing:
            logger.info("已有未完成任务，忽略重复发现: %s", file_path)
            return existing

        file_size = await wait_until_file_stable(
            file_path,
            timeout_seconds=settings.stable_timeout_seconds,
        )
        if file_size is None:
            logger.warning("文件不存在或未写稳: %s", file_path)
            return None

        # 写稳期间可能又来一次 created，再查一次避免双插
        existing = await self.task_repository.find_active_by_file_path(str(file_path))
        if existing:
            logger.info("已有未完成任务，忽略重复发现: %s", file_path)
            return existing

        file_name = file_path.name
        folder_name = file_path.parent.name
        folder_path = str(file_path.parent.resolve())
        need_preview = decision.need_single or decision.need_content
        # 策略拍进任务：这条的删文件/重试不随后续热更新改变
        policy = self.settings_hub.policy_for_new_task(need_preview)

        topic_id = None
        if settings.topic_creation_enabled and self.topic_creator is not None:
            topic_id = await self.topic_creator.get_or_create_topic(
                fold_path=folder_path,
                folder_name=folder_name,
                chat_id=decision.chat_id,
            )

        # preparing 调度器看不见，等封面写回才转 pending
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
        )
        self.settings_hub.remember_policy(task_id, policy)
        logger.info("已入库 task=%s: %s", task_id, file_path)

        if not need_preview:
            self.rescheduler.request_reschedule()
            return task_id

        page_path = None
        try:
            if decision.need_single:
                page_path = await FirstFramePreview(page_dir=settings.page_dir).extract_first_frame_async(
                    file_path, None
                )
            if decision.need_content:
                page_path = await GridPreview(page_dir=settings.page_dir).build_content_page_async(
                    file_path, None
                )
            await self.task_repository.update_preview(task_id, str(page_path) if page_path else None, True)
        except asyncio.CancelledError:
            await self.task_repository.update_preview(task_id, None, False, "preview cancelled")
            raise
        except Exception as error:
            # 封面失败仍转 pending，只发视频，不卡死在 preparing
            await self.task_repository.update_preview(task_id, None, False, str(error))
            logger.exception("预览生成失败 task=%s", task_id)
        self.rescheduler.request_reschedule()
        return task_id

    async def consume(self, file_queue: asyncio.Queue[Path | None]) -> None:
        """串行消费发现队列。单条失败只记日志；None 或 stop() 后退出。"""
        self._file_queue = file_queue
        while self._running:
            try:
                file_path = await file_queue.get()
            except asyncio.CancelledError:
                break
            if file_path is None:
                file_queue.task_done()
                break
            task = asyncio.create_task(self.handle_new_file(file_path))
            self._current_task = task
            try:
                await task
            except asyncio.CancelledError:
                if not self._running:
                    break
                raise
            except Exception:
                logger.exception("处理发现文件失败: %s", file_path)
            finally:
                self._current_task = None
                file_queue.task_done()

    async def stop(self) -> None:
        """不再接新文件，并取消正在跑的入库（含 ffmpeg 子进程）。"""
        self._running = False
        current = self._current_task
        if current is not None and not current.done():
            current.cancel()
        if self._file_queue is not None:
            try:
                self._file_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        if current is not None and not current.done():
            try:
                await current
            except (asyncio.CancelledError, Exception):
                pass
