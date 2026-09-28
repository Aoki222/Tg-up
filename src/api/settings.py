"""上传配置 API 的请求体。只覆盖 upload.toml，不碰 .env。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ChatAliasPayload(BaseModel):
    chat_id: int
    alias: str = ""
    title: str = ""

    @field_validator("chat_id")
    @classmethod
    def chat_id_nonzero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("chat_id 不能为 0")
        return value


class FolderRoutePayload(BaseModel):
    path: str
    chat_id: int = 0
    name: str = ""
    topic_enabled: bool | None = None
    enabled: bool = True
    platform: Literal["telegram", "gdrive"] = "telegram"
    dest_id: str = ""

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("路由路径不能为空")
        return text

    @field_validator("dest_id")
    @classmethod
    def dest_strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def fill_destination(self):
        if self.platform == "telegram" and not self.dest_id and self.chat_id:
            self.dest_id = str(self.chat_id)
        if not self.dest_id:
            raise ValueError("路由需要目标")
        if self.platform == "telegram" and not self.chat_id and self.dest_id.lstrip("-").isdigit():
            self.chat_id = int(self.dest_id)
        return self


class DriveFolderPayload(BaseModel):
    folder_id: str
    name: str = ""

    @field_validator("folder_id")
    @classmethod
    def folder_not_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("folder id 不能为空")
        return text


class SettingsPayload(BaseModel):
    chat_id: int
    observer_paths: list[str] = Field(default_factory=lambda: ["download"])
    observer_path: str | None = None
    page_dir: str = "page"
    archive_dir: str = "uploaded"
    preview: Literal["off", "first_frame", "grid"] = "off"
    topic_creation_enabled: bool = True
    after_success: Literal["keep", "delete", "move_to_archive"] = "keep"
    # 账号级上传并发固定为 1，文件内部并发由 FastTelethon 负责。
    concurrency: int = Field(default=1, ge=1, le=32)
    max_retries: int = Field(default=3, ge=1, le=20)
    upload_timeout_seconds: int = Field(default=1200, ge=30)
    assigned_timeout_seconds: int = Field(default=600, ge=30)
    stable_timeout_seconds: float = Field(default=1800, ge=1)
    watch_extensions: list[str] | None = None
    video_extensions: list[str] | None = None
    routes: list[FolderRoutePayload] = Field(default_factory=list)
    chats: list[ChatAliasPayload] = Field(default_factory=list)
    drive_folders: list[DriveFolderPayload] = Field(default_factory=list)
