"""独立预览池：入库后异步截图，不堵 FileIngestor。

并发默认 2，避免小 CPU 上同时开太多 ffmpeg。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ...adapters.task_store import TaskRepository
from ...domain.settings_hub import SettingsHub
from ...logger import get_logger
from ...ports.rescheduler import Rescheduler
from ...utils.video_preview import FirstFramePreview, GridPreview

logger = get_logger(__name__)

PREVIEW_CONCURRENCY = 2


@dataclass(frozen=True)
class PreviewJob:
    task_id: int
    file_path: Path
    need_single: bool
    need_content: bool


class PreviewPool:
    def __init__(
        self,
        task_repository: TaskRepository,
        rescheduler: Rescheduler,
        settings_hub: SettingsHub,
        concurrency: int = PREVIEW_CONCURRENCY,
    ):
        self.task_repository = task_repository
        self.rescheduler = rescheduler
        self.settings_hub = settings_hub
        self.concurrency = max(1, concurrency)
        self.queue: asyncio.Queue[PreviewJob | None] = asyncio.Queue()
        self._running = True
        self._inflight: set[asyncio.Task] = set()
        self._gate = asyncio.Semaphore(self.concurrency)

    def submit(self, job: PreviewJob) -> None:
        self.queue.put_nowait(job)

    async def run_forever(self) -> None:
        try:
            while self._running:
                job = await self.queue.get()
                if job is None:
                    self.queue.task_done()
                    break
                task = asyncio.create_task(self._run_guarded(job))
                self._inflight.add(task)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        self._running = False
        for task in list(self._inflight):
            task.cancel()
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self._inflight:
            await asyncio.wait(self._inflight, timeout=3)

    async def _run_guarded(self, job: PreviewJob) -> None:
        try:
            async with self._gate:
                await self._run(job)
        except asyncio.CancelledError:
            await self.task_repository.update_preview(job.task_id, None, False, "preview cancelled")
        except Exception as error:
            await self.task_repository.update_preview(job.task_id, None, False, str(error))
            logger.exception("预览生成失败 task=%s", job.task_id)
        finally:
            self.queue.task_done()
            current = asyncio.current_task()
            if current is not None:
                self._inflight.discard(current)
            self.rescheduler.request_reschedule()

    async def _run(self, job: PreviewJob) -> None:
        page_dir = self.settings_hub.get().page_dir
        page_path = None
        if job.need_single:
            page_path = await FirstFramePreview(page_dir=page_dir).extract_first_frame_async(
                job.file_path, None
            )
        if job.need_content:
            page_path = await GridPreview(page_dir=page_dir).build_content_page_async(
                job.file_path, None
            )
        await self.task_repository.update_preview(
            job.task_id, str(page_path) if page_path else None, True
        )
        logger.info("预览完成 task=%s", job.task_id)
