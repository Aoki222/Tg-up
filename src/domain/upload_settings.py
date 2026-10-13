"""可热更新的上传策略。进程身份（API / 数据库路径）不放这里。

监听目录在 observer_paths，可多条，保存后热挂 watchdog。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .task import AfterSuccess


@dataclass(frozen=True)
class ChatAlias:
    """用户添加的群/频道。alias 空则显示 title（官方名）。"""

    chat_id: int
    alias: str = ""
    title: str = ""


@dataclass(frozen=True)
class FolderRoute:
    """一条目录规则。topic_enabled 为 None 时跟随全局开关。未命中不回退默认群。"""

    path: Path
    chat_id: int
    name: str = ""
    topic_enabled: bool | None = None
    enabled: bool = True
    platform: str = "telegram"
    dest_id: str = ""
    # None 表示这项没写，继续往上继承。空字符串表示明确不要说明。
    caption_template: str | None = None
    preview: PreviewMode | None = None
    grid_layout: str | None = None
    # None 表示没写。off 不用话题，auto 按文件夹名，fixed 使用 topic_id。
    topic_mode: str | None = None
    topic_id: int | None = None
    auto_blocked: bool = False
    # 0 表示发现后立刻走原来的写稳。直播目录可设 180，大小连续安静这么多秒再入库。
    settle_delay_seconds: int = 0


@dataclass(frozen=True)
class DriveFolder:
    """手动添加的 Google Drive 文件夹。"""

    folder_id: str
    name: str = ""


@dataclass(frozen=True)
class ObserverRoot:
    """物理监听根目录及其监控状态。"""

    path: Path
    enabled: bool = True


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
    routes: tuple[FolderRoute, ...] = ()
    chats: tuple[ChatAlias, ...] = ()
    drive_folders: tuple[DriveFolder, ...] = ()
    # 新发现的过大视频按最少段数自动切片。已经停在「过大」里的不补切。
    auto_slice: bool = False
    # 路由都没写说明时用这份。空字符串表示不写说明。
    caption_template: str = ""
    # 全局自动监控总开关
    watch_enabled: bool = True
    # 带启停状态的物理根目录列表
    observer_roots: tuple[ObserverRoot, ...] = ()
    # oldest 入库顺序，natural 自然排序，created 创建时间，smallest 文件大小。
    schedule_order: str = "oldest"
    # 网格预览图规格，例如 "3x5", "4x3", "3x3", "4x4", "2x4"
    grid_layout: str = "3x5"
    # 小图右下角是否显示时间戳
    grid_timestamp: bool = True
    # 顶部是否显示视频信息横幅
    grid_metadata: bool = True

