import asyncio
from pathlib import Path
from ..logger import get_logger
from .task_repository import TaskRepository
from .rescheduler import Rescheduler
from .ingest_policy import IngestPolicy
from ..utils.video_preview import FirstFramePreview, GridPreview 

logger = get_logger(__name__)

async def wait_until_file_stable(file_path: Path, check_interval: float = 2.0, stable_rounds: int = 3) -> int | None:
    """文件大小连续 stable_rounds 次不变才算写完，不存在返回 None。"""
    last_size = -1
    stable_hits = 0
    while True:
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
    """职责：文件稳定 -> 写库 -> 唤醒调度器。不碰 watchdog。"""

    def __init__(self, task_repository: TaskRepository, rescheduler: Rescheduler):
        self.task_repository = task_repository
        self.rescheduler = rescheduler

    async def handle_new_file(self, file_path: Path) -> None:
        # 1. 等写稳，不存在直接忽略
        file_size = await wait_until_file_stable(file_path)
        if file_size is None:
            logger.warning("文件不存在: %s", file_path)
            return
        # 2. 决议：判需哪种图、发往哪个 topic
        decision = IngestPolicy().decide(file_path)
        # 3. 入库：快照落 single/content，有图则 preparing（调度器看不见）
        task_id = await self.task_repository.add_task(
            file_path=str(file_path), file_name=file_path.name, file_size=file_size,
            folder_name=file_path.parent.name, single_page=int(decision.need_single),
            content_page=int(decision.need_content), chat_id=decision.chat_id,
            status="pending" if not (decision.need_single or decision.need_content) else "preparing",
        )
        logger.info("已入库 task=%s: %s", task_id, file_path)
        # 4. 无图任务直接放行
        if not decision.need_single and not decision.need_content:
            self.rescheduler.request_reschedule()
            return
        # 5. 有图暂不 notify；to_thread 跑阻塞的 ffmpeg+PIL，不卡主循环
        single_path = content_path = None
        try:
            if decision.need_single:
                single_path = await FirstFramePreview().extract_first_frame_async(file_path, None)
            if decision.need_content:
                content_path = await GridPreview().build_content_page_async(file_path, None)
            # 6. 成功：主图优先网格，回填并转 pending 放行
            await self.task_repository.update_preview(task_id, str(content_path or single_path), True)
        except Exception as error:
            # 7. 失败/超时：保留快照，page_path 置空/半值，转 pending 只发视频
            await self.task_repository.update_preview(task_id, None, False, str(error))
            logger.exception("预览生成失败 task=%s", task_id)
        self.rescheduler.request_reschedule()