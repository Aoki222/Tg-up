import asyncio
import time
from pathlib import Path

from ..domain.settings_hub import SettingsHub
from ..domain.task import TaskStatus
from ..logger import get_logger
from ..utils.topic_creactor import TopicCreator
from ..utils.video_preview import FirstFramePreview, GridPreview
from .ingest_policy import IngestPolicy
from .rescheduler import Rescheduler
from .task_repository import TaskRepository

logger = get_logger(__name__)


async def wait_until_file_stable(
    file_path: Path,
    check_interval: float = 2.0,
    stable_rounds: int = 3,
    timeout_seconds: float = 1800,
) -> int | None:
    """文件大小连续 stable_rounds 次不变才算写完；超时或不存在返回 None。"""
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

    async def handle_new_file(self, file_path: Path) -> None:
        settings = self.settings_hub.get()
        file_path = file_path.resolve()
        decision = self.ingest_policy.decide(file_path, settings)
        if not decision.allowed:
            logger.info("跳过非目标文件: %s", file_path)
            return

        if await self.task_repository.find_active_by_file_path(str(file_path)):
            logger.info("已有未完成任务，忽略重复发现: %s", file_path)
            return

        file_size = await wait_until_file_stable(
            file_path,
            timeout_seconds=settings.stable_timeout_seconds,
        )
        if file_size is None:
            logger.warning("文件不存在或未写稳: %s", file_path)
            return

        if await self.task_repository.find_active_by_file_path(str(file_path)):
            logger.info("已有未完成任务，忽略重复发现: %s", file_path)
            return

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
        )
        self.settings_hub.remember_policy(task_id, policy)
        logger.info("已入库 task=%s: %s", task_id, file_path)

        if not need_preview:
            self.rescheduler.request_reschedule()
            return

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
        except Exception as error:
            await self.task_repository.update_preview(task_id, None, False, str(error))
            logger.exception("预览生成失败 task=%s", task_id)
        self.rescheduler.request_reschedule()

    async def consume(self, file_queue: asyncio.Queue[Path]) -> None:
        while True:
            file_path = await file_queue.get()
            try:
                await self.handle_new_file(file_path)
            except Exception:
                logger.exception("处理发现文件失败: %s", file_path)
            finally:
                file_queue.task_done()
