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
        # watchdog 线程回调：只转发，不做业务，业务跳回主循环执行
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
        # 1. 现取主循环，保证转发目标与调度器同 loop
        event_loop = asyncio.get_running_loop()
        # 2. 启动系统级目录监听
        self.observer.schedule(
            WatchdogEventAdapter(event_loop, self.handle_new_file),
            str(self.watch_path),
            recursive=True,
        )
        self.observer.start()
        # 3. 常驻等待，stop() 置 stop_event 后退出
        await self.stop_event.wait()

    async def stop(self) -> None:
        # 幂等停止：先放行等待，再停线程，避免二次 join 抛错
        self.stop_event.set()
        if self.observer.is_alive():
            self.observer.stop()
            await asyncio.to_thread(self.observer.join)