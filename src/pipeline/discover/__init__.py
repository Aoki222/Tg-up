"""发现层：watchdog 快路径，启动扫盘补漏。"""

from .scan import iter_existing_files
from .watcher import FolderWatcher

__all__ = ["FolderWatcher", "iter_existing_files"]
