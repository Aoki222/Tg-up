"""发现层之后的入库。

顺序：路径是否命中路由 → 扩展名过滤 → 路径去重 → 等文件写完 → 再建话题 → 入库。
未命中只写入 unmatched_files，不进上传队列。
写稳和建话题不占全局锁，多个文件可并行等待；只有「再查重 + INSERT」串行。
需要封面时写成 preparing 后立刻返回；截图由 PreviewPool 做完再转 pending。
封面失败不丢视频：page_path 置空，仍然上传。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import time

from ...adapters.task_store import TaskRepository
from ...domain import limits
from ...domain.caption import build_ingest_caption, find_nearest_route, resolve_preview
from ...domain.route_topic import resolve_topic
from ...domain.settings_hub import SettingsHub
from ...domain.task import AfterSuccess, TaskStatus
from ...domain.upload_settings import PreviewMode
from ...logger import get_logger
from ...ports.rescheduler import Rescheduler
from ...utils.topic_creactor import TopicCreator, TopicSessionUnavailable
from .filters import check_transient_file
from .policy import IngestPolicy, _PREVIEWABLE_SUFFIXES, _chat_id_of, _route_dest, path_is_inside_roots, watch_roots
from .preview import PreviewJob, PreviewPool

logger = get_logger(__name__)

# 同时等写稳 / 建话题的文件数。INSERT 仍由 _insert_lock 串行。
INGEST_CONCURRENCY = 8
# session 还没连上时先放回队列。启动扫描和连接是并行的，不能把文件直接丢掉。
SESSION_RETRY_SECONDS = 5.0


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
        try:
            current_size = file_path.stat().st_size
        except OSError:
            return None
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
        on_oversized=None,
    ):
        self.task_repository = task_repository
        self.rescheduler = rescheduler
        self.settings_hub = settings_hub
        self.topic_creator = topic_creator
        self.preview_pool = preview_pool
        self.on_oversized = on_oversized
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
        path_key = str(file_path)
        settings = self.settings_hub.get()
        decision = self.ingest_policy.decide(file_path, settings)
        if decision.is_transient:
            logger.debug("跳过下载中间/临时文件 [%s]: %s", decision.transient_reason, file_path.name)
            return None
        if not decision.allowed:
            existing = await self.task_repository.find_active_by_file_path(path_key)
            if existing:
                return existing
            if decision.is_disabled:
                logger.info("目录监控已单独禁用，不上传: %s", file_path)
            elif not decision.matched:
                logger.info("未命中路由，不上传: %s", file_path)
            else:
                logger.info("跳过非目标扩展名: %s", file_path)
            return None

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

    async def manual_dispatch(
        self,
        file_path: Path | str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        caption: str | None = None,
        after_success: str | None = None,
        need_preview: bool | None = None,
    ) -> dict:
        """手动选择文件投递上传，不依赖自动监控和等待写稳。"""
        resolved_path = Path(file_path).resolve()
        path_str = str(resolved_path)

        if not resolved_path.is_file():
            return {
                "ok": False,
                "reason": "文件不存在或不是常规文件",
                "file_path": path_str,
            }

        settings = self.settings_hub.get()
        if not path_is_inside_roots(resolved_path, watch_roots(settings)):
            return {
                "ok": False,
                "reason": "文件不在监听目录内",
                "file_path": path_str,
            }

        try:
            file_size = resolved_path.stat().st_size
        except OSError:
            return {
                "ok": False,
                "reason": "无法读取文件大小",
                "file_path": path_str,
            }

        transient_res = check_transient_file(resolved_path)
        if transient_res.is_transient:
            return {
                "ok": False,
                "reason": f"文件属于下载中间或临时文件 ({transient_res.reason})",
                "file_path": path_str,
            }

        existing = await self.task_repository.find_active_by_file_path(path_str)
        if existing:
            return {
                "ok": False,
                "reason": "已有未完成任务正在排队或上传",
                "task_id": existing,
                "file_path": path_str,
            }

        nearest_route = find_nearest_route(resolved_path, settings)

        # 决议目标群组
        if chat_id is not None:
            target_chat_id = int(chat_id)
            platform = "telegram"
            dest_id = str(target_chat_id)
        elif nearest_route is not None:
            target_chat_id = nearest_route.chat_id or _chat_id_of(nearest_route)
            platform = nearest_route.platform or "telegram"
            dest_id = _route_dest(nearest_route)
            if not target_chat_id and not dest_id:
                return {
                    "ok": False,
                    "reason": "匹配的路由未配置目标群组，且未手动指定 chat_id",
                    "file_path": path_str,
                }
        else:
            return {
                "ok": False,
                "reason": "未指定目标群组且该路径未匹配任何路由规则",
                "file_path": path_str,
            }

        # 决议话题
        if topic_id is not None:
            target_topic_id = int(topic_id)
        elif nearest_route is not None:
            topic_mode, fixed_topic_id = resolve_topic(resolved_path, settings)
            if platform != "telegram":
                topic_mode, fixed_topic_id = "off", None
            if topic_mode == "fixed" and fixed_topic_id is not None:
                target_topic_id = fixed_topic_id
            elif topic_mode == "auto" and self.topic_creator is not None:
                target_topic_id = await self.topic_creator.get_or_create_topic(
                    fold_path=str(resolved_path.parent.resolve()),
                    folder_name=resolved_path.parent.name,
                    chat_id=target_chat_id,
                )
            else:
                target_topic_id = None
        else:
            target_topic_id = None

        # 决议封面
        previewable = resolved_path.suffix.lower() in _PREVIEWABLE_SUFFIXES and platform == "telegram"
        preview_mode = resolve_preview(resolved_path, settings)
        if need_preview is True:
            if previewable:
                need_content = preview_mode is PreviewMode.GRID
                need_single = not need_content
            else:
                need_single = False
                need_content = False
        elif need_preview is False:
            need_single = False
            need_content = False
        else:
            need_single = previewable and preview_mode is PreviewMode.FIRST_FRAME
            need_content = previewable and preview_mode is PreviewMode.GRID

        has_preview = need_single or need_content

        # 决议说明文案
        if caption is not None and caption.strip():
            target_caption = caption.strip()
        else:
            target_caption = build_ingest_caption(resolved_path, settings, file_size)

        # 决议任务策略
        policy = self.settings_hub.policy_for_new_task(has_preview)
        if after_success:
            try:
                policy = replace(policy, after_success=AfterSuccess(after_success))
            except ValueError:
                pass

        file_name = resolved_path.name
        folder_name = resolved_path.parent.name

        async with self._insert_lock:
            existing = await self.task_repository.find_active_by_file_path(path_str)
            if existing:
                return {
                    "ok": False,
                    "reason": "已有未完成任务正在排队或上传",
                    "task_id": existing,
                    "file_path": path_str,
                }

            oversized = limits.telegram_bot_blocked(file_size, platform)
            if has_preview:
                status = TaskStatus.PREPARING
            elif oversized:
                status = TaskStatus.OVERSIZED
            else:
                status = TaskStatus.PENDING

            task_id = await self.task_repository.add_task(
                file_path=path_str,
                file_name=file_name,
                file_size=file_size,
                folder_name=folder_name,
                single_page=need_single,
                content_page=need_content,
                chat_id=target_chat_id if platform == "telegram" else 0,
                topic_id=target_topic_id,
                platform=platform,
                dest_id=dest_id,
                status=status.value,
                max_retries=policy.max_retries,
                caption=target_caption,
                after_success=policy.after_success.value,
                error_msg=limits.OVERSIZED_REASON if oversized and not has_preview else None,
            )
            self.settings_hub.remember_policy(task_id, policy)
            await self.task_repository.clear_unmatched(path_str)

        if oversized:
            logger.info("手动投递已入库（超过 2GB） task=%s size=%s: %s", task_id, file_size, resolved_path)
        else:
            logger.info("手动投递已入库 task=%s: %s", task_id, resolved_path)

        if oversized and not has_preview:
            await self._offer_slice(task_id)
        elif not has_preview:
            if self.rescheduler:
                self.rescheduler.request_reschedule()
        else:
            if self.preview_pool is None:
                await self.task_repository.update_preview(task_id, None, False, "preview pool missing")
                if oversized:
                    await self._offer_slice(task_id)
                elif self.rescheduler:
                    self.rescheduler.request_reschedule()
            else:
                self.preview_pool.submit(
                    PreviewJob(
                        task_id=task_id,
                        file_path=resolved_path,
                        need_single=need_single,
                        need_content=need_content,
                    )
                )

        return {
            "ok": True,
            "task_id": task_id,
            "status": status.value,
            "file_path": path_str,
            "file_name": file_name,
            "file_size": file_size,
        }

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
        if decision.is_transient:
            logger.debug("跳过下载中间/临时文件 [%s]: %s", decision.transient_reason, file_path.name)
            return None
        if not decision.matched:
            if settings.watch_extensions and file_path.suffix.lower() not in settings.watch_extensions:
                logger.info("跳过非目标文件: %s", file_path)
                return None
            await self.task_repository.record_unmatched(
                str(file_path),
                file_path.name,
                file_path.parent.name,
                file_size,
            )
            logger.info("未命中路由，不上传: %s", file_path)
            return None
        if decision.is_disabled:
            logger.info("目录监控已单独禁用，不上传: %s", file_path)
            return None
        if not decision.allowed:
            logger.info("跳过非目标文件: %s", file_path)
            return None
        await self.task_repository.clear_unmatched(str(file_path))

        file_name = file_path.name
        folder_name = file_path.parent.name
        if not caption:
            caption = build_ingest_caption(file_path, settings, file_size)
        folder_path = str(file_path.parent.resolve())
        need_preview = decision.need_single or decision.need_content
        policy = self.settings_hub.policy_for_new_task(need_preview)

        topic_id = None
        if decision.topic_mode == "fixed" and decision.fixed_topic_id:
            topic_id = decision.fixed_topic_id
        elif decision.topic_enabled and self.topic_creator is not None:
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
            platform = decision.platform or "telegram"
            oversized = limits.telegram_bot_blocked(file_size, platform)
            if need_preview:
                status = TaskStatus.PREPARING
            elif oversized:
                status = TaskStatus.OVERSIZED
            else:
                status = TaskStatus.PENDING
            task_id = await self.task_repository.add_task(
                file_path=str(file_path),
                file_name=file_name,
                file_size=file_size,
                folder_name=folder_name,
                single_page=decision.need_single,
                content_page=decision.need_content,
                chat_id=decision.chat_id if platform == "telegram" else 0,
                topic_id=topic_id,
                platform=platform,
                dest_id=decision.dest_id,
                status=status.value,
                max_retries=policy.max_retries,
                caption=caption,
                after_success=policy.after_success.value,
                error_msg=limits.OVERSIZED_REASON if oversized and not need_preview else None,
            )
            self.settings_hub.remember_policy(task_id, policy)

        if oversized:
            logger.info(
                "已入库（超过 2GB，不自动分配） task=%s size=%s: %s",
                task_id,
                file_size,
                file_path,
            )
        else:
            logger.info("已入库 task=%s: %s", task_id, file_path)

        if oversized and not need_preview:
            await self._offer_slice(task_id)
            return task_id

        if not need_preview:
            self.rescheduler.request_reschedule()
            return task_id

        if self.preview_pool is None:
            await self.task_repository.update_preview(task_id, None, False, "preview pool missing")
            if oversized:
                await self._offer_slice(task_id)
            else:
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

    async def _offer_slice(self, task_id: int) -> None:
        if self.on_oversized is None:
            return
        try:
            await self.on_oversized(task_id)
        except Exception:
            logger.exception("自动切片失败 task=%s", task_id)

    async def _wait_for_ingest_slot(self) -> None:
        """同时入库的文件不超过 concurrency。满了就等有一个结束再取下一条。"""
        while self._running:
            running = {task for task in self._inflight if not task.done()}
            self._inflight.intersection_update(running)
            if len(self._inflight) < self.concurrency:
                return
            await asyncio.wait(self._inflight, return_when=asyncio.FIRST_COMPLETED)

    async def consume(self, file_queue: asyncio.Queue[Path | None]) -> None:
        """并行消费发现队列。同时处理的文件有上限；None 或 stop() 后退出。"""
        self._file_queue = file_queue
        try:
            while self._running:
                await self._wait_for_ingest_slot()
                if not self._running:
                    break
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
        except TopicSessionUnavailable:
            logger.warning("暂无已连接的 Telegram session，稍后重试: %s", file_path)
            # 先让出并发名额，再把路径放回队列，避免队列满时和取任务互相等待。
            current = asyncio.current_task()
            if current is not None:
                self._inflight.discard(current)
            await self._requeue_when_session_may_connect(file_path)
        except Exception:
            logger.exception("处理发现文件失败: %s", file_path)
        finally:
            current = asyncio.current_task()
            if current is not None:
                self._inflight.discard(current)
            if self._file_queue is not None:
                self._file_queue.task_done()

    async def _requeue_when_session_may_connect(self, file_path: Path) -> None:
        try:
            await asyncio.sleep(SESSION_RETRY_SECONDS)
        except asyncio.CancelledError:
            if not self._running:
                return
            raise
        if not self._running or self._file_queue is None:
            return
        await self._file_queue.put(file_path)

    async def stop(self) -> None:
        """不再接新文件，并取消正在跑的入库（含写稳等待）。"""
        self._running = False
        for task in list(self._inflight):
            task.cancel()
        queue = self._file_queue
        if queue is not None:
            # 队列满时丢掉一个待扫路径，也要把停止哨兵放进去，否则消费循环会卡在 get。
            while True:
                try:
                    queue.put_nowait(None)
                    break
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    queue.task_done()
        pending = list(self._inflight)
        if pending:
            await asyncio.wait(pending, timeout=3)
