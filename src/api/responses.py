"""校验看板和配置的对外字典。通过后仍返回原字典，避免漏写的字段被丢掉。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class BoardTaskModel(_Loose):
    id: int
    file_name: str
    file_size: int
    folder_name: str | None = None
    status: str
    assigned_worker: str | None = None
    platform: str
    dest_id: str
    retry_count: int
    max_retries: int
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    percent: float
    current: float
    total: float
    speed_bps: float
    eta_seconds: float
    stage: str | None = None
    message: str
    sliceable: bool
    slice_phase: str
    slice_min_parts: int | None = None
    part_index: int | None = None
    part_count: int | None = None


class BoardCountsModel(_Loose):
    preparing: int
    pending: int
    assigned: int
    uploading: int
    oversized: int
    failed: int
    success: int
    success_today: int


class BoardSnapshotModel(_Loose):
    items: list[BoardTaskModel]
    counts: BoardCountsModel


class StatusPageModel(_Loose):
    items: list[BoardTaskModel]
    total: int
    page: int
    page_size: int


class ObserverInfoModel(_Loose):
    path: str
    ok: bool
    error: str


class ObserverRootModel(ObserverInfoModel):
    enabled: bool


class RoutePublicModel(_Loose):
    name: str
    path: str
    chat_id: int
    topic_enabled: bool | None = None
    enabled: bool
    platform: str
    dest_id: str
    caption_template: str | None = None
    preview: str | None = None
    grid_layout: str | None = None
    topic_mode: str | None = None
    topic_id: int | None = None
    auto_blocked: bool
    settle_delay_seconds: int = 0


class ChatPublicModel(_Loose):
    chat_id: int
    alias: str
    title: str


class DriveFolderPublicModel(_Loose):
    name: str
    folder_id: str


class SettingsPublicModel(_Loose):
    revision: int
    schedule_order: Literal["oldest", "natural", "created", "smallest"]
    chat_id: int
    watch_enabled: bool
    observer_paths: list[str]
    observer_path_infos: list[ObserverInfoModel]
    observer_roots: list[ObserverRootModel]
    page_dir: str
    archive_dir: str
    preview: str
    grid_layout: str = "3x5"
    grid_timestamp: bool = True
    grid_metadata: bool = True
    caption_template: str
    topic_creation_enabled: bool
    after_success: str
    auto_slice: bool
    concurrency: int
    max_retries: int
    upload_timeout_seconds: int
    assigned_timeout_seconds: int
    stable_timeout_seconds: float
    watch_extensions: list[str]
    routes: list[RoutePublicModel]
    chats: list[ChatPublicModel]
    drive_folders: list[DriveFolderPublicModel]


def publish(model: type[BaseModel], payload: dict) -> dict:
    """结构不对就抛错。对了就原样返回，多出来的键也留着。"""
    model.model_validate(payload)
    return payload
