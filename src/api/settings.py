"""上传配置 API 的请求体。只覆盖 upload.toml，不碰 .env。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SettingsPayload(BaseModel):
    chat_id: int
    observer_paths: list[str] = Field(default_factory=lambda: ["download"])
    observer_path: str | None = None
    page_dir: str = "page"
    archive_dir: str = "uploaded"
    preview: Literal["off", "first_frame", "grid"] = "off"
    topic_creation_enabled: bool = True
    after_success: Literal["keep", "delete", "move_to_archive"] = "keep"
    concurrency: int = Field(default=3, ge=1, le=32)
    max_retries: int = Field(default=3, ge=1, le=20)
    upload_timeout_seconds: int = Field(default=1200, ge=30)
    assigned_timeout_seconds: int = Field(default=600, ge=30)
    stable_timeout_seconds: float = Field(default=1800, ge=1)
    watch_extensions: list[str] | None = None
    video_extensions: list[str] | None = None
