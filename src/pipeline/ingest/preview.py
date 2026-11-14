"""独立预览池：入库后异步截图，不堵 FileIngestor。

封面和上传抢同一台机器的 CPU。默认只跑 1 路，再配合 ffmpeg 单线程，
把另一颗核留给分块加密和发包。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ...adapters.task_store import TaskRepository
from ...domain import limits
from ...domain.caption import resolve_grid_layout
from ...domain.settings_hub import SettingsHub
from ...logger import get_logger
from ...ports.rescheduler import Rescheduler
from ...utils.video_preview import FirstFramePreview, GridPreview, NoVideoStreamError, VideoThumbnail

logger = get_logger(__name__)

PREVIEW_CONCURRENCY = 1


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
        on_settled=None,
    ):
        self.task_repository = task_repository
        self.rescheduler = rescheduler
        self.settings_hub = settings_hub
        self.concurrency = max(1, concurrency)
        self.queue: asyncio.Queue[PreviewJob | None] = asyncio.Queue()
        self._running = True
        self._inflight: set[asyncio.Task] = set()
        self._gate = asyncio.Semaphore(self.concurrency)
        self.on_settled = on_settled

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
        except NoVideoStreamError as error:
            await self._reject_unplayable(job.task_id, str(error))
            logger.warning("没有视频流，不再上传 task=%s: %s", job.task_id, error)
        except FileNotFoundError as error:
            await self.task_repository.update_preview(job.task_id, None, False, str(error))
            logger.warning("预览跳过 task=%s: %s", job.task_id, error)
        except Exception as error:
            await self.task_repository.update_preview(job.task_id, None, False, str(error))
            logger.exception("预览生成失败 task=%s", job.task_id)
        finally:
            if self.on_settled is not None:
                try:
                    await self.on_settled(job.task_id)
                except Exception:
                    logger.exception("预览结束后的切片检查失败 task=%s", job.task_id)
            self.queue.task_done()
            current = asyncio.current_task()
            if current is not None:
                self._inflight.discard(current)
            self.rescheduler.request_reschedule()

    async def _reject_unplayable(self, task_id: int, reason: str) -> None:
        """没有画面的文件停在失败，不再放进上传队列。"""
        row = await self.task_repository.get_task_by_id(task_id)
        max_retries = int((row or {}).get("max_retries") or 1)
        await self.task_repository.mark_task_failed(task_id, max_retries, max_retries, reason)

    async def _run(self, job: PreviewJob) -> None:
        try:
            size = job.file_path.stat().st_size
        except OSError:
            size = None
        if size is not None and size < limits.MIN_PLAYABLE_BYTES:
            raise NoVideoStreamError(f"{limits.EMPTY_MEDIA_REASON}: {job.file_path.name}")
        page_dir = self.settings_hub.get().page_dir
        page_path = None
        if job.need_single:
            page_path = await FirstFramePreview(page_dir=page_dir).extract_first_frame_async(
                job.file_path, None
            )
        if job.need_content:
            settings = self.settings_hub.get()
            grid_layout = resolve_grid_layout(job.file_path, settings)
            page_path = await GridPreview(
                grid_layout=grid_layout,
                show_timestamp=settings.grid_timestamp,
                show_metadata=settings.grid_metadata,
                page_dir=page_dir,
            ).build_content_page_async(job.file_path, None)
        try:
            await VideoThumbnail(page_dir=page_dir).ensure_thumbnail_async(
                job.file_path, Path(page_path) if page_path else None
            )
        except Exception:
            logger.debug("生成视频缩略图失败（不影响主流程）", exc_info=True)
        await self.task_repository.update_preview(
            job.task_id, str(page_path) if page_path else None, True
        )
        logger.info("预览完成 task=%s", job.task_id)
