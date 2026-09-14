"""可热更新的上传策略。进程身份（API / 数据库路径）不放这里。

监听目录在 observer_paths，可多条，保存后热挂 watchdog。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .task import AfterSuccess


class PreviewMode(StrEnum):
    OFF = "off"
    FIRST_FRAME = "first_frame"
    GRID = "grid"


@dataclass(frozen=True)
class UploadSettings:
    """可热更新的上传策略。进程身份（API/session/数据库）不在这里。"""

    chat_id: int
    observer_paths: tuple[Path, ...]
    page_dir: Path
    archive_dir: Path
    preview: PreviewMode
    topic_creation_enabled: bool
    after_success: AfterSuccess
    concurrency: int
    max_retries: int
    upload_timeout_seconds: int
    assigned_timeout_seconds: int
    stable_timeout_seconds: float
    # 空集合 = 监听任意后缀；非空则只收这些后缀（含点，如 .mp4）
    watch_extensions: frozenset[str]
