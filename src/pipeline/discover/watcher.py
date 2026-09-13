"""目录监听。只负责发现路径，不写库、不等文件写完。

一个 Observer 可挂多条路径；apply_paths 热更新时增删 watch，不必重启进程。
watchdog 回调跑在后台线程，必须用 run_coroutine_threadsafe 丢回主循环。
Windows 上剪切/移动往往走 on_moved 而不是 on_created，两个都要接。
启动时目录里已有的文件不会触发事件，需要 scan.py 补扫。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ...logger import get_logger

logger = get_logger(__name__)


class WatchdogEventAdapter(FileSystemEventHandler):
    """职责：把 watchdog 线程事件转交到 asyncio 主循环。"""

    def __init__(self, event_loop: asyncio.AbstractEventLoop, handle_new_file: Callable[[Path], Awaitable[None]]):
        super().__init__()
        self.event_loop = event_loop
        self.handle_new_file = handle_new_file

    def on_created(self, event) -> None:
        if not event.is_directory:
            asyncio.run_coroutine_threadsafe(
                self.handle_new_file(Path(event.src_path)),
                self.event_loop,
            )

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        destination = getattr(event, "dest_path", None)
        if destination:
            asyncio.run_coroutine_threadsafe(
                self.handle_new_file(Path(destination)),
                self.event_loop,
            )


class FolderWatcher:
    """一条 Observer 线程，多条监听路径；路径集合可热更新。"""

    def __init__(self, handle_new_file: Callable[[Path], Awaitable[None]]):
        self.stop_event = asyncio.Event()
        self.handle_new_file = handle_new_file
        self.observer = Observer()
        self._adapter: WatchdogEventAdapter | None = None
        self._desired: list[Path] = []
        self._scheduled: dict[str, object] = {}

    def apply_paths(self, paths: Sequence[Path]) -> list[Path]:
        """对齐要监听的目录。返回这次新加上的路径（调用方应对新目录扫盘）。"""
        desired: list[Path] = []
        seen: set[str] = set()
        for raw in paths:
            resolved = raw.resolve()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            desired.append(resolved)
        previous = set(self._scheduled)
        self._desired = desired
        if self._adapter is not None:
            self._sync_observer()
        added = [path for path in desired if str(path) not in previous]
        return added

    async def run_forever(self) -> None:
        event_loop = asyncio.get_running_loop()
        self._adapter = WatchdogEventAdapter(event_loop, self.handle_new_file)
        self._sync_observer()
        self.observer.start()
        try:
            await self.stop_event.wait()
        except asyncio.CancelledError:
            raise
        finally:
            await self._join_observer()

    async def stop(self) -> None:
        self.stop_event.set()
        await self._join_observer()

    def _sync_observer(self) -> None:
        if self._adapter is None:
            return
        wanted = {str(path): path for path in self._desired}
        for key in list(self._scheduled):
            if key not in wanted:
                try:
                    self.observer.unschedule(self._scheduled.pop(key))
                except KeyError:
                    self._scheduled.pop(key, None)
                logger.info("已停止监听: %s", key)
        for key, path in wanted.items():
            if key in self._scheduled:
                continue
            if not path.is_dir():
                logger.warning("跳过监听（目录不存在或不是目录）: %s", path)
                continue
            watch = self.observer.schedule(self._adapter, str(path), recursive=True)
            self._scheduled[key] = watch
            logger.info("已开始监听: %s", path)

    async def _join_observer(self) -> None:
        if self.observer.is_alive():
            self.observer.stop()
            try:
                await asyncio.wait_for(asyncio.to_thread(self.observer.join), timeout=2)
            except TimeoutError:
                logger.warning("watchdog 线程未能及时退出")
            self._scheduled.clear()
