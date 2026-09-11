"""目录监听。只负责发现路径，不写库、不等文件写完。

watchdog 回调跑在后台线程，必须用 run_coroutine_threadsafe 丢回主循环。
Windows 上剪切/移动往往走 on_moved 而不是 on_created，两个都要接。
启动时目录里已有的文件不会触发事件，需要 scan.py 补扫。
"""

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class WatchdogEventAdapter(FileSystemEventHandler):
    """职责：把 watchdog 线程事件转交到 asyncio 主循环。"""

    def __init__(self, event_loop: asyncio.AbstractEventLoop, handle_new_file: Callable[[Path], Awaitable[None]]):
        super().__init__()
        self.event_loop = event_loop
        self.handle_new_file = handle_new_file

    def on_created(self, event) -> None:
        if not event.is_directory:
            # watchdog 在后台线程；业务必须丢回 asyncio 主循环
            asyncio.run_coroutine_threadsafe(
                self.handle_new_file(Path(event.src_path)),
                self.event_loop,
            )

    def on_moved(self, event) -> None:
        # Windows 剪切/移动走 moved，不走 created
        if event.is_directory:
            return
        destination = getattr(event, "dest_path", None)
        if destination:
            asyncio.run_coroutine_threadsafe(
                self.handle_new_file(Path(destination)),
                self.event_loop,
            )


class FolderWatcher:
    """职责：只监听目录，业务逻辑全部委托给回调。"""

    def __init__(self, watch_path: Path, handle_new_file: Callable[[Path], Awaitable[None]]):
        self.stop_event = asyncio.Event()
        self.watch_path = watch_path
        self.handle_new_file = handle_new_file
        self.observer = Observer()

    async def run_forever(self) -> None:
        """挂上系统级目录监听后一直等到 stop()。"""
        self.watch_path.mkdir(parents=True, exist_ok=True)
        event_loop = asyncio.get_running_loop()
        self.observer.schedule(
            WatchdogEventAdapter(event_loop, self.handle_new_file),
            str(self.watch_path),
            recursive=True,
        )
        self.observer.start()
        await self.stop_event.wait()

    async def stop(self) -> None:
        self.stop_event.set()
        if self.observer.is_alive():
            self.observer.stop()
            await asyncio.to_thread(self.observer.join)
