"""过大视频的切片队列。一次只跑一个 ffmpeg。

切完的分段留在 data/slices/<任务 id>/，不搬进监听目录。
每一段按当前封面设置进入预览池，再和普通文件一样排队上传。
源文件留在监听目录，直到每一段都上传成功，再按原任务的收尾策略处理。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..adapters.task_store import TaskRepository
from ..domain.settings_hub import SettingsHub
from ..domain.slices import (
    MSG_CUTTING,
    MSG_INTERRUPTED,
    MSG_NO_DISK,
    MSG_TOO_DENSE,
    MSG_WAIT,
    SLICE_MAX_PARTS,
    is_sliceable_video,
    minimum_slice_parts,
    msg_part_failed,
    msg_released,
    part_caption,
    user_dispatch_blocked,
)
from ..domain.task import Task, task_from_row
from ..logger import get_logger
from ..ports.after_upload import AfterUpload
from ..ports.rescheduler import Rescheduler
from .ingest.preview import PreviewJob
from .slice_media import SliceCutError, cut_into_parts, directory_has_room, remove_tree

logger = get_logger(__name__)


class SliceService:
    """手动切片和自动切片共用这一条队列。"""

    def __init__(
        self,
        repository: TaskRepository,
        settings_hub: SettingsHub,
        rescheduler: Rescheduler,
        after_upload: AfterUpload,
        project_dir: Path,
    ):
        self.repository = repository
        self.settings_hub = settings_hub
        self.rescheduler = rescheduler
        self.after_upload = after_upload
        self.project_dir = project_dir
        self.queue: asyncio.Queue[int | None] = asyncio.Queue()
        self.preview_pool = None
        self._running = True
        self._cancelled: set[int] = set()
        self._current: asyncio.Task | None = None

    def slice_dir(self, parent_id: int) -> Path:
        return self.project_dir / "data" / "slices" / str(parent_id)

    async def request(self, parent_id: int, parts: int) -> str:
        """确认切片。返回 ok / not_found / not_oversized / not_video / missing_file / busy / bad_parts:N。"""
        self._cancelled.discard(parent_id)
        row = await self.repository.get_task_by_id(parent_id)
        if row is None:
            return "not_found"
        if str(row.get("status")) != "oversized" or row.get("parent_id"):
            return "not_oversized"
        source = Path(str(row.get("file_path") or ""))
        if not is_sliceable_video(source):
            return "not_video"
        if not source.is_file():
            return "missing_file"
        if await self.repository.has_slice_plan(parent_id) or user_dispatch_blocked(row.get("error_msg")):
            return "busy"
        minimum = minimum_slice_parts(int(row.get("file_size") or 0))
        count = int(parts)
        if count < minimum or count > SLICE_MAX_PARTS:
            return f"bad_parts:{minimum}"
        accepted = await self.repository.begin_slice(parent_id, count, MSG_WAIT)
        if not accepted:
            return "busy"
        self.queue.put_nowait(parent_id)
        logger.info("切片已排队 task=%s parts=%s", parent_id, count)
        return "ok"

    async def offer_auto(self, task_id: int) -> None:
        """新进入「过大」的视频，在开关打开时按最少段数排队。已经停着的任务不补切。"""
        if not self.settings_hub.get().auto_slice:
            return
        row = await self.repository.get_task_by_id(task_id)
        if row is None or str(row.get("status")) != "oversized" or row.get("parent_id"):
            return
        if int(row.get("slice_parts") or 0) > 0 or await self.repository.has_slice_plan(task_id):
            return
        source = Path(str(row.get("file_path") or ""))
        if not is_sliceable_video(source):
            return
        parts = minimum_slice_parts(int(row.get("file_size") or 0))
        if parts > SLICE_MAX_PARTS:
            await self.repository.set_oversized_note(task_id, MSG_TOO_DENSE, clear_parts=True)
            return
        await self.request(task_id, parts)

    async def continue_upload(self, parent_id: int) -> str:
        """从失败的那一段接着传，不重新切片。"""
        row = await self.repository.get_task_by_id(parent_id)
        if row is None:
            return "not_found"
        if str(row.get("status")) != "oversized":
            return "not_oversized"
        failed = await self.repository.failed_slice(parent_id)
        if failed is None:
            return "not_failed"
        segment = Path(str(failed.get("segment_path") or ""))
        if not segment.is_file():
            return "missing_file"
        child_id = failed.get("child_task_id")
        if child_id:
            queued = await self.repository.requeue_slice_child(int(child_id))
            if not queued:
                return "not_failed"
            await self.repository.mark_slice(int(failed["id"]), "queued", int(child_id))
        else:
            await self._insert_child(row, failed)
        count = int(failed["part_count"])
        await self.repository.set_oversized_note(parent_id, msg_released(count))
        self.rescheduler.request_reschedule()
        logger.info("切片继续 task=%s part=%s/%s", parent_id, int(failed["part_index"]), count)
        return "ok"

    async def on_child(self, task: Task, outcome: str) -> None:
        """一段上传结束。成功后看是不是全部完成；失败则记在源卡片上，其它段继续。"""
        if not task.parent_id:
            return
        slice_row = await self.repository.slice_by_child(task.id)
        if slice_row is None:
            return
        parent_id = int(slice_row["parent_id"])
        if outcome == "success":
            await self.repository.mark_slice(int(slice_row["id"]), "success")
            if await self.repository.slices_all_success(parent_id):
                await self._finish(parent_id)
            return
        if outcome != "failed":
            return
        fresh = await self.repository.get_task_by_id(task.id)
        if fresh is None or str(fresh.get("status")) != "failed":
            return
        await self.repository.mark_slice(int(slice_row["id"]), "failed")
        index = int(slice_row["part_index"])
        count = int(slice_row["part_count"])
        await self.repository.set_oversized_note(parent_id, msg_part_failed(index, count))
        logger.warning("切片段上传失败 task=%s part=%s/%s", parent_id, index, count)

    async def discard(self, parent_id: int) -> tuple[str, list[int]]:
        """清除分段任务和副本。有段正在上传时整次不动。源文件不动。

        返回 (结果, 已删除的分段 id)。结果是 ok 或 uploading。
        """
        rows = await self.repository.list_slices(parent_id)
        for row in rows:
            child_id = row.get("child_task_id")
            if not child_id:
                continue
            child = await self.repository.get_task_by_id(int(child_id))
            if child is not None and str(child.get("status")) in {"assigned", "uploading"}:
                return "uploading", []
        self._cancelled.add(parent_id)
        child_ids: list[int] = []
        for row in rows:
            child_id = row.get("child_task_id")
            if not child_id:
                continue
            child = await self.repository.get_task_by_id(int(child_id))
            if child is not None:
                self._unlink_cover(child.get("page_path"))
            child_ids.append(int(child_id))
        await self.repository.delete_task_ids(child_ids)
        await self.repository.delete_slice_plan(parent_id)
        remove_tree(self.slice_dir(parent_id))
        return "ok", child_ids

    async def clear_failed_part(self, child_id: int) -> bool:
        """清掉失败列里的一段。副本留在 data/slices，源文件不动。"""
        row = await self.repository.get_task_by_id(child_id)
        if row is None or str(row.get("status")) != "failed" or not row.get("parent_id"):
            return False
        self._unlink_cover(row.get("page_path"))
        await self.repository.detach_slice_child(child_id)
        await self.repository.delete_task_ids([child_id])
        return True

    async def clear_failed_parts(self, child_ids: list[int] | None = None) -> int:
        ids = child_ids if child_ids is not None else await self.repository.failed_slice_child_ids()
        cleared = 0
        for child_id in ids:
            if await self.clear_failed_part(int(child_id)):
                cleared += 1
        return cleared

    async def sync_requeued_parts(self) -> None:
        """失败列上的重试把分段打回队列后，源卡片改回「已进入队列」。"""
        for parent_id in await self.repository.slice_parents_to_recover():
            moved = False
            for row in await self.repository.list_slices(parent_id):
                if str(row.get("status")) != "failed" or not row.get("child_task_id"):
                    continue
                child = await self.repository.get_task_by_id(int(row["child_task_id"]))
                if child is None or str(child.get("status")) == "failed":
                    continue
                await self.repository.mark_slice(int(row["id"]), "queued", int(row["child_task_id"]))
                moved = True
            if not moved:
                continue
            if await self.repository.failed_slice(parent_id) is not None:
                continue
            slices = await self.repository.list_slices(parent_id)
            if slices:
                await self.repository.set_oversized_note(parent_id, msg_released(len(slices)))

    async def recover(self) -> None:
        """分段表还在就从下一段接着传。ffmpeg 跑到一半则记切片中断，不自动重切。"""
        for parent_id in await self.repository.slice_parents_to_recover():
            row = await self.repository.get_task_by_id(parent_id)
            if row is None or str(row.get("status")) != "oversized":
                continue
            if await self.repository.has_slice_plan(parent_id):
                await self._adopt_plan(parent_id)
                continue
            note = str(row.get("error_msg") or "")
            if note == MSG_CUTTING:
                remove_tree(self.slice_dir(parent_id))
                await self.repository.set_oversized_note(parent_id, MSG_INTERRUPTED, clear_parts=True)
                continue
            if note == MSG_WAIT and int(row.get("slice_parts") or 0) > 0:
                self.queue.put_nowait(parent_id)

    async def run_forever(self) -> None:
        try:
            while self._running:
                parent_id = await self.queue.get()
                if parent_id is None:
                    self.queue.task_done()
                    break
                self._current = asyncio.create_task(self._slice_parent(parent_id))
                try:
                    await self._current
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("切片失败 task=%s", parent_id)
                    await self._mark_interrupted(parent_id)
                finally:
                    self._current = None
                    self.queue.task_done()
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        self._running = False
        current = self._current
        if current is not None and not current.done():
            current.cancel()
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def _slice_parent(self, parent_id: int) -> None:
        if parent_id in self._cancelled:
            return
        row = await self.repository.get_task_by_id(parent_id)
        if row is None or str(row.get("status")) != "oversized":
            return
        if await self.repository.has_slice_plan(parent_id):
            await self._adopt_plan(parent_id)
            return
        parts = int(row.get("slice_parts") or 0)
        source = Path(str(row.get("file_path") or ""))
        if parts < 1 or not source.is_file():
            await self.repository.set_oversized_note(parent_id, "文件不存在", clear_parts=True)
            return
        directory = self.slice_dir(parent_id)
        remove_tree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        await self.repository.set_oversized_note(parent_id, MSG_CUTTING)
        need = source.stat().st_size
        if not directory_has_room(directory, need):
            remove_tree(directory)
            await self.repository.set_oversized_note(parent_id, MSG_NO_DISK, clear_parts=True)
            return
        try:
            segments = await cut_into_parts(source, directory, parts)
        except asyncio.CancelledError:
            raise
        except SliceCutError as error:
            remove_tree(directory)
            await self.repository.set_oversized_note(parent_id, error.message, clear_parts=True)
            logger.info("切片停住 task=%s: %s", parent_id, error.message)
            return
        except Exception:
            logger.exception("ffmpeg 切片失败 task=%s", parent_id)
            await self._mark_interrupted(parent_id)
            return
        fresh = await self.repository.get_task_by_id(parent_id)
        if (
            parent_id in self._cancelled
            or fresh is None
            or str(fresh.get("status")) != "oversized"
        ):
            remove_tree(directory)
            return
        await self.repository.insert_slice_plan(
            parent_id, [(str(path), size) for path, size in segments]
        )
        await self._enqueue_parts(parent_id)

    async def _mark_interrupted(self, parent_id: int) -> None:
        if parent_id in self._cancelled:
            return
        row = await self.repository.get_task_by_id(parent_id)
        if row is None or str(row.get("status")) != "oversized":
            return
        if await self.repository.has_slice_plan(parent_id):
            return
        remove_tree(self.slice_dir(parent_id))
        await self.repository.set_oversized_note(parent_id, MSG_INTERRUPTED, clear_parts=True)

    async def _adopt_plan(self, parent_id: int) -> None:
        """重启后接着已有分段。已经入库的留给封面池和调度器，不再切一次。"""
        parent = await self.repository.get_task_by_id(parent_id)
        if parent is None:
            return
        for row in await self.repository.list_slices(parent_id):
            status = str(row.get("status"))
            child_id = row.get("child_task_id")
            if status == "planned" or not child_id:
                if status != "failed":
                    await self._insert_child(parent, row)
                continue
            child = await self.repository.get_task_by_id(int(child_id))
            if child is None:
                await self.repository.mark_slice(int(row["id"]), "failed")
                continue
            child_status = str(child.get("status"))
            if child_status == "success":
                await self.repository.mark_slice(int(row["id"]), "success")
            elif child_status == "failed":
                await self.repository.mark_slice(int(row["id"]), "failed")
        if await self.repository.slices_all_success(parent_id):
            await self._finish(parent_id)
            return
        failed = await self.repository.failed_slice(parent_id)
        if failed is not None:
            await self.repository.set_oversized_note(
                parent_id,
                msg_part_failed(int(failed["part_index"]), int(failed["part_count"])),
            )
            return
        slices = await self.repository.list_slices(parent_id)
        if slices:
            await self.repository.set_oversized_note(parent_id, msg_released(len(slices)))
        self.rescheduler.request_reschedule()

    async def _enqueue_parts(self, parent_id: int) -> None:
        if parent_id in self._cancelled:
            return
        parent = await self.repository.get_task_by_id(parent_id)
        if parent is None or str(parent.get("status")) != "oversized":
            return
        planned = [
            row
            for row in await self.repository.list_slices(parent_id)
            if str(row.get("status")) == "planned"
        ]
        for row in planned:
            await self._insert_child(parent, row)
        if planned:
            await self.repository.set_oversized_note(parent_id, msg_released(len(planned)))
        self.rescheduler.request_reschedule()

    async def _insert_child(self, parent: dict, planned: dict) -> None:
        index = int(planned["part_index"])
        count = int(planned["part_count"])
        segment = Path(str(planned.get("segment_path") or ""))
        caption = part_caption(str(parent.get("caption") or ""), index, count)
        need_single, need_content = self._preview_flags(segment)
        status = "preparing" if need_single or need_content else "pending"
        child_id = await self.repository.insert_slice_child(
            parent,
            part_index=index,
            part_count=count,
            file_path=str(segment),
            file_name=segment.name,
            file_size=int(planned.get("segment_size") or 0),
            caption=caption,
            status=status,
            after_success=self._part_after_success(parent),
            single_page=1 if need_single else 0,
            content_page=1 if need_content else 0,
        )
        await self.repository.mark_slice(int(planned["id"]), "queued", child_id)
        pool = self.preview_pool
        if status == "preparing" and pool is not None:
            pool.submit(
                PreviewJob(
                    task_id=child_id,
                    file_path=segment,
                    need_single=need_single,
                    need_content=need_content,
                )
            )
        logger.info(
            "切片段入队 task=%s part=%s/%s status=%s file=%s",
            parent["id"],
            index,
            count,
            status,
            segment.name,
        )

    def _preview_flags(self, path: Path) -> tuple[bool, bool]:
        settings = self.settings_hub.get()
        mode = getattr(settings, "preview", None)
        name = getattr(mode, "value", mode)
        if not is_sliceable_video(path):
            return False, False
        if name == "first_frame":
            return True, False
        if name == "grid":
            return False, True
        return False, False

    def _part_after_success(self, parent: dict) -> str:
        settings = self.settings_hub.get()
        raw = getattr(settings, "after_success", None)
        text = getattr(raw, "value", raw)
        if text in {"keep", "delete", "move_to_archive"}:
            return str(text)
        return str(parent.get("after_success") or "keep")

    async def _finish(self, parent_id: int) -> None:
        parent = await self.repository.get_task_by_id(parent_id)
        if parent is None or str(parent.get("status")) == "success":
            self._remove_empty_slice_dir(parent_id)
            return
        remote = await self.repository.latest_child_remote_id(parent_id)
        await self.repository.mark_task_succeeded(parent_id, remote)
        policy = self.settings_hub.policy_for_row(parent)
        task = task_from_row(parent, policy)
        try:
            await self.after_upload.handle(task)
        except Exception:
            logger.exception("切片全部完成后收尾失败 task=%s", parent_id)
        self._remove_empty_slice_dir(parent_id)
        await self.repository.delete_slice_plan(parent_id)
        logger.info("切片全部上传完成 task=%s", parent_id)

    def _remove_empty_slice_dir(self, parent_id: int) -> None:
        directory = self.slice_dir(parent_id)
        if not directory.is_dir():
            return
        if any(directory.iterdir()):
            return
        directory.rmdir()

    def _unlink_cover(self, page_path: object) -> None:
        if not page_path:
            return
        path = Path(str(page_path))
        page_dir = getattr(self.settings_hub.get(), "page_dir", None)
        if page_dir is None:
            return
        try:
            path.resolve().relative_to(Path(page_dir).resolve())
        except (OSError, ValueError):
            return
        path.unlink(missing_ok=True)
