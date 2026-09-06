from pathlib import Path
import asyncio
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from typing import Callable


from ..logger import get_logger
from ..database.create import insert_single_task

# __main__
from ..config import OBSERVER_PATH
from ..database.init import init_db

logger = get_logger(__name__)


class Handler(FileSystemEventHandler):
    
    def __init__(self, loop: asyncio.AbstractEventLoop, notify_callback: Callable[[],None] | None):
        super().__init__()
        self._loop = loop
        self._notify_callback = notify_callback

    def on_created(self, event):
        if not event.is_directory:            
                        
            file_path = Path(event.src_path)
            
            asyncio.run_coroutine_threadsafe(
                self.process_file(file_path),
                self._loop,
            )
            
    async def wait_file_stable(self, path: Path, interval: float = 2.0, stable_count: int = 3) -> int | None:
        """等待文件大小稳定，返回最终文件大小；文件不存在则返回 None。"""

        last_size = -1
        stable = 0

        while True:
            if not path.is_file():
                return None

            size = path.stat().st_size

            if size == last_size:
                stable += 1

                if stable >= stable_count:
                    return size
            else:
                stable = 0
                last_size = size

            await asyncio.sleep(interval)
            
    async def process_file(self, file_path: Path):

        file_size = await self.wait_file_stable(
            path=file_path
        )
        
        file_name = file_path.name
        folder_name = file_path.parent.name

        if file_size is None:
            logger.warning("文件不存在: %s", file_path)
            return

        await insert_single_task(
            file_path=str(file_path),
            file_name=file_name,
            file_size=file_size,
            folder_name=folder_name,
        )

        logger.info(
            "以加入数据库: %s，大小: %.3f MB",
            file_path,
            file_size / (1024 * 1024),
        )
        if self._notify_callback:
            try:
                self._notify_callback()
            except Exception as e:
                logger.exception("唤醒调度器时发生异常: %s", e)
                


class FolderWatcher:
    def __init__(self, path, recursive=True, notify_callback = None):
        self._path = path # path为监听的文件夹路径
        self._recursive = recursive
        self._handler = None
        self._observer = Observer()
        self._notify = notify_callback

    async def start(self):
        loop = asyncio.get_running_loop()
        self._handler = Handler(loop, notify_callback=self._notify)  # 这里可以传入一个回调函数，用于通知调度器
        self._observer.schedule(self._handler, self._path, recursive=self._recursive)
        self._observer.start()

    async def stop(self):
        self._observer.stop()
        self._observer.join()
        
        
if __name__ == "__main__":
    async def main():
        await init_db()
        watcher = FolderWatcher(OBSERVER_PATH)
        await watcher.start()
        try:
            # 让事件循环一直挂起，直到收到 Ctrl+C
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await watcher.stop()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到退出信号，停止监控")