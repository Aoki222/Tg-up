"""上传成功后的本地收尾。

行为来自 task.policy.after_success（入库快照），不是此刻的 upload.toml。
keep：不动文件。delete：删视频和封面。move_to_archive：按相对监听目录挪到 uploaded/。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..domain.settings_hub import SettingsHub
from ..domain.task import AfterSuccess, Task
from ..logger import get_logger

logger = get_logger(__name__)


class ConfigurableAfterUpload:
    def __init__(self, settings_hub: SettingsHub):
        self.settings_hub = settings_hub

    async def handle(self, task: Task) -> None:
        # 用入库时拍下的策略，避免热更新改到一半的任务
        action = task.policy.after_success
        paths = [Path(task.artifacts.video_path)]
        if task.artifacts.page_path:
            paths.append(Path(task.artifacts.page_path))

        if action is AfterSuccess.KEEP:
            return
        if action is AfterSuccess.DELETE:
            for path in paths:
                path.unlink(missing_ok=True)
            logger.info("已按配置删除本地文件: %s", task.file_path)
            return
        if action is AfterSuccess.MOVE_TO_ARCHIVE:
            settings = self.settings_hub.get()
            for path in paths:
                if not path.exists():
                    continue
                destination = _archive_destination(path, settings.observer_path, settings.archive_dir)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(destination))
            logger.info("已按配置归档: %s -> %s", task.file_path, settings.archive_dir)


def _archive_destination(source: Path, observer_path: Path, archive_dir: Path) -> Path:
    """尽量保留 download/a/b.mp4 → uploaded/a/b.mp4；不在监听根下则只保留文件名。"""
    try:
        relative = source.resolve().relative_to(observer_path.resolve())
    except ValueError:
        relative = Path(source.name)
    return archive_dir / relative
