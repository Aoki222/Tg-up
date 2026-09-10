from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

from ..logger import get_logger
from .task import AfterSuccess, TaskPolicy
from .upload_settings import PreviewMode, UploadSettings

logger = get_logger(__name__)

_DEFAULT_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v")


def _as_path(project_dir: Path, value: str | None, default: str) -> Path:
    path = Path(value if value else default)
    if not path.is_absolute():
        path = project_dir / path
    return path


def _as_extensions(raw: object) -> frozenset[str]:
    if not raw:
        return frozenset(_DEFAULT_EXTENSIONS)
    items = raw if isinstance(raw, list) else [raw]
    normalized = []
    for item in items:
        text = str(item).strip().lower()
        if not text:
            continue
        if not text.startswith("."):
            text = f".{text}"
        normalized.append(text)
    return frozenset(normalized) or frozenset(_DEFAULT_EXTENSIONS)


def _as_bool(raw: object, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_upload_settings(config_path: Path, project_dir: Path) -> UploadSettings:
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    chat_id_raw = data.get("chat_id")
    if chat_id_raw in (None, 0, ""):
        env_chat_id = os.getenv("TARGET_CHAT_ID")
        if not env_chat_id:
            raise RuntimeError("upload.toml 未配置 chat_id，且环境变量 TARGET_CHAT_ID 也未设置")
        chat_id = int(env_chat_id)
    else:
        chat_id = int(chat_id_raw)

    preview = PreviewMode(str(data.get("preview", "off")))
    after_success = AfterSuccess(str(data.get("after_success", "keep")))

    return UploadSettings(
        chat_id=chat_id,
        observer_path=_as_path(project_dir, data.get("observer_path"), "download"),
        page_dir=_as_path(project_dir, data.get("page_dir"), "page"),
        archive_dir=_as_path(project_dir, data.get("archive_dir"), "uploaded"),
        preview=preview,
        topic_creation_enabled=_as_bool(data.get("topic_creation_enabled"), True),
        after_success=after_success,
        concurrency=max(1, int(data.get("concurrency", 3))),
        max_retries=max(1, int(data.get("max_retries", 3))),
        upload_timeout_seconds=max(1, int(data.get("upload_timeout_seconds", 1200))),
        assigned_timeout_seconds=max(1, int(data.get("assigned_timeout_seconds", 600))),
        stable_timeout_seconds=max(1.0, float(data.get("stable_timeout_seconds", 1800))),
        video_extensions=_as_extensions(data.get("video_extensions")),
    )


def ensure_upload_config(project_dir: Path) -> Path:
    config_path = project_dir / "upload.toml"
    example_path = project_dir / "upload.toml.example"
    if not config_path.exists():
        if not example_path.exists():
            raise RuntimeError(f"缺少上传配置：{config_path} 且没有 {example_path}")
        shutil.copy(example_path, config_path)
        logger.info("已复制 upload.toml.example -> upload.toml，请按需修改后热更新即可生效")
    return config_path


class SettingsHub:
    """内存中只保留一份冻结配置，文件变更后整体替换。"""

    def __init__(self, config_path: Path, project_dir: Path):
        self.config_path = config_path
        self.project_dir = project_dir
        self._settings = load_upload_settings(config_path, project_dir)
        self._mtime = config_path.stat().st_mtime
        self._policies: dict[int, TaskPolicy] = {}

    def get(self) -> UploadSettings:
        return self._settings

    def policy_for_new_task(self, need_preview: bool) -> TaskPolicy:
        settings = self._settings
        return TaskPolicy(
            need_preview=need_preview,
            after_success=settings.after_success,
            max_retries=settings.max_retries,
        )

    def remember_policy(self, task_id: int, policy: TaskPolicy) -> None:
        self._policies[task_id] = policy

    def policy_for_row(self, row: dict) -> TaskPolicy:
        task_id = int(row["id"])
        stored = self._policies.get(task_id)
        if stored is not None:
            return stored
        need_preview = bool(row.get("single_page") or row.get("content_page"))
        return TaskPolicy(
            need_preview=need_preview,
            after_success=self._settings.after_success,
            max_retries=int(row.get("max_retries") or self._settings.max_retries),
        )

    def reload_if_changed(self) -> bool:
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError:
            logger.warning("读取上传配置失败，保持旧配置: %s", self.config_path)
            return False
        if mtime == self._mtime:
            return False
        try:
            loaded = load_upload_settings(self.config_path, self.project_dir)
        except Exception:
            logger.exception("upload.toml 无效，保持上一份配置")
            return False
        self._settings = loaded
        self._mtime = mtime
        logger.info(
            "已热更新上传配置: chat_id=%s preview=%s after_success=%s concurrency=%s",
            loaded.chat_id,
            loaded.preview,
            loaded.after_success,
            loaded.concurrency,
        )
        return True
