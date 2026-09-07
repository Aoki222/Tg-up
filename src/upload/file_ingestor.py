import asyncio
from pathlib import Path
from ..logger import get_logger
from .task_repository import TaskRepository
from .rescheduler import Rescheduler

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
        file_size = await wait_until_file_stable(file_path)
        if file_size is None:
            logger.warning("文件不存在: %s", file_path)
            return
        await self.task_repository.add_task(
            file_path=str(file_path),
            file_name=file_path.name,
            file_size=file_size,
            folder_name=file_path.parent.name,
        )
        logger.info("已加入数据库: %s, 大小: %.3f MB", file_path, file_size / (1024 * 1024))
        try:
            self.rescheduler.request_reschedule()
        except Exception:
            logger.exception("唤醒调度器失败: %s", file_path)