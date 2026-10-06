"""进度实现：内存总线（给 SSE）+ 控制台/日志进度条。

Worker 只调用 report()。Hub 节流后再广播，避免 Telethon 每个分片都打满前端。
快照里只留正在上传的任务。成功、失败、退回等待会先推给已连接的页面，再从内存删掉。
超过 60 秒没有新进度的记录在取快照时过期。
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace

from ..domain.progress import UploadProgress, is_album_units
from ..logger import get_logger

logger = get_logger(__name__)

# 断线重连中间大约二十多秒没有新回调，60 秒盖得住，又不会把已删除的进度留太久。
PROGRESS_TTL_SECONDS = 60.0


class ProgressHub:
    """进程内进度总线。FastAPI SSE 订阅这里；Worker 只负责 report。"""

    def __init__(self) -> None:
        self._latest: dict[int, UploadProgress] = {}
        self._queues: list[asyncio.Queue[UploadProgress]] = []
        self._last_emit: dict[int, tuple[float, float]] = {}
        # task_id -> (time, current, total, ema_speed)
        self._speed_state: dict[int, tuple[float, float, float, float]] = {}
        self._touched: dict[int, float] = {}

    def snapshot(self) -> list[UploadProgress]:
        """当前仍在 uploading 的进度，给 SSE 连上时的第一批快照。"""
        self._expire()
        return list(self._latest.values())

    def forget(self, task_ids) -> None:
        """任务已离开上传或已从库里删除。不广播假进度。"""
        for task_id in task_ids:
            self._drop(int(task_id))

    def subscribe(self) -> asyncio.Queue[UploadProgress]:
        queue: asyncio.Queue[UploadProgress] = asyncio.Queue(maxsize=256)
        self._queues.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[UploadProgress]) -> None:
        try:
            self._queues.remove(queue)
        except ValueError:
            pass

    def report(self, progress: UploadProgress) -> None:
        """同步接口，可从 Telethon 回调里调用。队列满则丢掉最旧的一条。"""
        progress = self._with_speed(progress)
        now = time.monotonic()
        self._touched[progress.task_id] = now
        if progress.stage == "uploading":
            self._latest[progress.task_id] = progress
            if not self._should_emit(progress):
                return
            self._last_emit[progress.task_id] = (now, progress.percent)
            self._broadcast(progress)
            return
        self._broadcast(progress)
        self._drop(progress.task_id)

    def _drop(self, task_id: int) -> None:
        self._latest.pop(task_id, None)
        self._last_emit.pop(task_id, None)
        self._speed_state.pop(task_id, None)
        self._touched.pop(task_id, None)

    def _expire(self) -> None:
        now = time.monotonic()
        stale = [
            task_id
            for task_id, seen in self._touched.items()
            if now - seen > PROGRESS_TTL_SECONDS
        ]
        for task_id in stale:
            self._drop(task_id)

    def _with_speed(self, progress: UploadProgress) -> UploadProgress:
        """在节流之前用单调时钟算 EMA 速度。相册单位不算字节速度。"""
        if progress.stage != "uploading":
            self._speed_state.pop(progress.task_id, None)
            return replace(progress, speed_bps=0.0, eta_seconds=-1.0)

        now = time.monotonic()
        previous = self._speed_state.get(progress.task_id)
        # 相册回调的单位是文件个数。不参与字节速度，也不清掉已经量到的速度。
        if is_album_units(progress.current, progress.total):
            speed = previous[3] if previous is not None else 0.0
            return replace(progress, speed_bps=round(speed, 1), eta_seconds=-1.0)

        if previous is None:
            self._speed_state[progress.task_id] = (now, progress.current, progress.total, 0.0)
            return replace(progress, speed_bps=0.0, eta_seconds=-1.0)

        last_t, last_current, last_total, last_speed = previous
        if last_total != progress.total:
            self._speed_state[progress.task_id] = (now, progress.current, progress.total, 0.0)
            return replace(progress, speed_bps=0.0, eta_seconds=-1.0)

        speed = last_speed
        dt = now - last_t
        db = progress.current - last_current
        # 窗口没满就保持起点。每个分片都重置的话，高速上传永远凑不满 0.2 秒。
        if dt >= 0.2 and db > 0:
            instant = db / dt
            speed = instant if last_speed <= 0 else (0.55 * last_speed + 0.45 * instant)
            self._speed_state[progress.task_id] = (now, progress.current, progress.total, speed)
        remaining = max(0.0, progress.total - progress.current)
        eta = remaining / speed if speed > 0 else -1.0
        return replace(progress, speed_bps=round(speed, 1), eta_seconds=eta)

    def _should_emit(self, progress: UploadProgress) -> bool:
        """uploading 约 1% 或 0.4 秒才推一次；非 uploading 立刻推。"""
        if progress.stage != "uploading":
            return True
        previous = self._last_emit.get(progress.task_id)
        if previous is None:
            return True
        last_time, last_percent = previous
        if progress.percent >= 100 or progress.percent <= 0:
            return True
        if progress.percent - last_percent >= 1.0:
            return True
        if time.monotonic() - last_time >= 0.4:
            return True
        return False

    def _broadcast(self, progress: UploadProgress) -> None:
        stale: list[asyncio.Queue[UploadProgress]] = []
        for queue in self._queues:
            try:
                queue.put_nowait(progress)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(progress)
                except asyncio.QueueFull:
                    stale.append(queue)
        for queue in stale:
            self.unsubscribe(queue)


class LogProgressBar:
    """终端进度条；日志文件只在关键百分比打一行，避免刷屏。"""

    def __init__(self, width: int = 24) -> None:
        self.width = width
        self._last_log_percent: dict[int, int] = {}
        self._tty = sys.stderr.isatty()

    def report(self, progress: UploadProgress) -> None:
        bar = _render_bar(progress.percent, self.width)
        size = _render_size(progress.current, progress.total)
        line = (
            f"[{progress.worker_name}] {progress.file_name} "
            f"{bar} {progress.percent:5.1f}% {size}"
        )
        if progress.message:
            line = f"{line}  {progress.message}"

        if self._tty and progress.stage == "uploading":
            sys.stderr.write("\r" + line[:120].ljust(120))
            sys.stderr.flush()
        if progress.stage != "uploading":
            if self._tty:
                sys.stderr.write("\r" + " " * 120 + "\r")
                sys.stderr.flush()
            logger.info("%s", line)
            self._last_log_percent.pop(progress.task_id, None)
            return

        bucket = int(progress.percent // 10) * 10
        last = self._last_log_percent.get(progress.task_id)
        if last is None or bucket >= last + 10 or progress.percent >= 100:
            logger.info("%s", line)
            self._last_log_percent[progress.task_id] = bucket


class FanoutReporter:
    """一份进度同时给总线和日志条。"""

    def __init__(self, *reporters) -> None:
        self._reporters = reporters

    def report(self, progress: UploadProgress) -> None:
        for reporter in self._reporters:
            try:
                reporter.report(progress)
            except Exception:
                logger.exception("进度汇报失败: %s", type(reporter).__name__)

    def forget(self, task_ids) -> None:
        for reporter in self._reporters:
            forget = getattr(reporter, "forget", None)
            if forget is None:
                continue
            try:
                forget(task_ids)
            except Exception:
                logger.exception("清理进度失败: %s", type(reporter).__name__)


def _render_bar(percent: float, width: int) -> str:
    filled = min(width, int(round(width * percent / 100.0)))
    if filled >= width:
        return "[" + "=" * width + "]"
    if filled <= 0:
        return "[" + " " * width + "]"
    return "[" + "=" * (filled - 1) + ">" + " " * (width - filled) + "]"


def _render_size(current: float, total: float) -> str:
    # 相册回调里 total 可能是文件个数而不是字节
    if is_album_units(current, total):
        return f"{current:.1f}/{total:.0f} files"
    return f"{_fmt_bytes(current)}/{_fmt_bytes(total)}"


def _fmt_bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(number)}{unit}"
            return f"{number:.1f}{unit}"
        number /= 1024
    return f"{value:.0f}B"
