import asyncio
from pathlib import Path
from typing import Callable, Awaitable
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
            asyncio.run_coroutine_threadsafe(
                self.handle_new_file(Path(event.src_path)),
                self.event_loop,
            )

class FolderWatcher:
    """职责：只监听目录，业务逻辑全部委托给 on_file_ready 回调。"""

    def __init__(self, watch_path: Path, handle_new_file: Callable[[Path], Awaitable[None]]):
        self.stop_event = asyncio.Event()
        
        self.watch_path = watch_path
        self.handle_new_file = handle_new_file
        self.observer = Observer()

    async def run_forever(self) -> None:
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
        self.observer.stop()
        await asyncio.to_thread(self.observer.join)