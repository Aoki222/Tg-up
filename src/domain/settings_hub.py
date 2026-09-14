"""upload.toml 的加载与热更新。

和 .env 分开：.env 是进程身份（API），改完要重启；
upload.toml 是上传策略，保存后约 2 秒生效。

热更新是整份替换冻结对象。解析失败则保持上一份，避免坏文件把服务弄死。
已入库任务的 after_success / max_retries 看内存里的 policy 快照；
进程重启后快照丢失，只能退回当时的当前配置（尚未把这两项写入表）。
"""

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


def _rel_path(project_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_upload_toml(payload: dict) -> str:
    if payload.get("watch_extensions") is not None:
        extensions = payload.get("watch_extensions")
    else:
        extensions = payload.get("video_extensions") or []
    if isinstance(extensions, str):
        extensions = [part.strip() for part in extensions.split(",") if part.strip()]
    cleaned = []
    for item in extensions:
        text = str(item).strip().lower().lstrip(".")
        if text:
            cleaned.append(text)
    ext_list = ", ".join(_toml_string(item) for item in cleaned)
    topic = "true" if payload.get("topic_creation_enabled", True) else "false"
    return (
        "# 由配置页写入。保存后热加载，不必重启。\n"
        f"chat_id = {int(payload['chat_id'])}\n"
        f"observer_paths = [{_path_list(payload)}]\n"
        f"page_dir = {_toml_string(str(payload.get('page_dir') or 'page'))}\n"
        f"archive_dir = {_toml_string(str(payload.get('archive_dir') or 'uploaded'))}\n"
        f"preview = {_toml_string(str(payload.get('preview') or 'off'))}\n"
        f"topic_creation_enabled = {topic}\n"
        f"after_success = {_toml_string(str(payload.get('after_success') or 'keep'))}\n"
        f"concurrency = {max(1, int(payload.get('concurrency', 3)))}\n"
        f"max_retries = {max(1, int(payload.get('max_retries', 3)))}\n"
        f"upload_timeout_seconds = {max(1, int(payload.get('upload_timeout_seconds', 1200)))}\n"
        f"assigned_timeout_seconds = {max(1, int(payload.get('assigned_timeout_seconds', 600)))}\n"
        f"stable_timeout_seconds = {max(1.0, float(payload.get('stable_timeout_seconds', 1800)))}\n"
        f"watch_extensions = [{ext_list}]\n"
    )


def _path_list(payload: dict) -> str:
    raw = payload.get("observer_paths")
    if not raw:
        single = payload.get("observer_path")
        raw = [single] if single else []
    if isinstance(raw, str):
        raw = [raw]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        key = str(_resolve_observer_path(text))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(key)
    return ", ".join(_toml_string(item) for item in cleaned)


def _resolve_observer_path(value: str) -> Path:
    """相对路径相对进程 cwd，绝对路径原样 resolve。不创建目录。"""
    path = Path(value.strip().strip('"').strip("'")).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def inspect_observer_path(path: Path) -> dict:
    text = str(path)
    if not path.exists():
        return {
            "path": text,
            "ok": False,
            "error": "目录不存在（Docker 里请填容器路径，如 /app/download，不是宿主机的 /home/...）",
        }
    if not path.is_dir():
        return {"path": text, "ok": False, "error": "不是目录"}
    return {"path": text, "ok": True, "error": ""}


def _as_observer_paths(_project_dir: Path, data: dict) -> tuple[Path, ...]:
    raw = data.get("observer_paths")
    if raw is None:
        single = data.get("observer_path")
        raw = [single] if single else []
    if isinstance(raw, str):
        raw = [raw]
    paths: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip() if item is not None else ""
        if not text:
            continue
        path = _resolve_observer_path(text)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return tuple(paths)


def _as_path(project_dir: Path, value: str | None, default: str) -> Path:
    path = Path(value if value else default)
    if not path.is_absolute():
        path = project_dir / path
    return path


def _as_watch_extensions(data: dict) -> frozenset[str]:
    """空列表 = 任意格式。未配置时兼容旧字段 video_extensions。"""
    if "watch_extensions" in data:
        raw = data.get("watch_extensions")
    elif "video_extensions" in data:
        raw = data.get("video_extensions")
    else:
        raw = [ext.lstrip(".") for ext in _DEFAULT_EXTENSIONS]
    if not raw:
        return frozenset()
    items = raw if isinstance(raw, list) else [raw]
    normalized = []
    for item in items:
        text = str(item).strip().lower()
        if not text:
            continue
        if not text.startswith("."):
            text = f".{text}"
        normalized.append(text)
    return frozenset(normalized)


def _as_bool(raw: object, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_upload_settings(config_path: Path, project_dir: Path) -> UploadSettings:
    """读一份 toml。chat_id 为 0 或省略时回退环境变量 TARGET_CHAT_ID。"""
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
        observer_paths=_as_observer_paths(project_dir, data),
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
        watch_extensions=_as_watch_extensions(data),
    )


def ensure_upload_config(project_dir: Path) -> Path:
    """配置放 data/upload.toml，避免 Docker 单文件挂载导致保存 EBUSY。

    若根目录还有旧的 upload.toml 文件，启动时复制进 data/。
    """
    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "upload.toml"
    example_path = project_dir / "upload.toml.example"
    legacy = project_dir / "upload.toml"
    if config_path.is_file():
        return config_path
    if legacy.is_file():
        shutil.copy(legacy, config_path)
        logger.info("已将 upload.toml 迁到 %s", config_path)
        return config_path
    if not example_path.exists():
        raise RuntimeError(f"缺少上传配置：{config_path} 且没有 {example_path}")
    shutil.copy(example_path, config_path)
    logger.info("已复制 upload.toml.example -> %s", config_path)
    return config_path


class SettingsHub:
    """upload.toml 的内存副本：整份替换，解析失败保留旧配置。

    过程参数（并发、超时）立刻用新值。
    已入库任务的删文件/重试策略看 Task.policy，不跟热更新走。
    """

    def __init__(self, config_path: Path, project_dir: Path):
        self.config_path = config_path
        self.project_dir = project_dir
        self._settings = load_upload_settings(config_path, project_dir)
        self._mtime = config_path.stat().st_mtime
        self._policies: dict[int, TaskPolicy] = {}

    def get(self) -> UploadSettings:
        return self._settings

    def public_dict(self) -> dict:
        """给前端的可编辑字段。监听目录一律返回绝对路径及是否存在。"""
        settings = self._settings
        observer_infos = [inspect_observer_path(path) for path in settings.observer_paths]
        return {
            "chat_id": settings.chat_id,
            "observer_paths": [item["path"] for item in observer_infos],
            "observer_path_infos": observer_infos,
            "page_dir": _rel_path(self.project_dir, settings.page_dir),
            "archive_dir": _rel_path(self.project_dir, settings.archive_dir),
            "preview": settings.preview.value,
            "topic_creation_enabled": settings.topic_creation_enabled,
            "after_success": settings.after_success.value,
            "concurrency": settings.concurrency,
            "max_retries": settings.max_retries,
            "upload_timeout_seconds": settings.upload_timeout_seconds,
            "assigned_timeout_seconds": settings.assigned_timeout_seconds,
            "stable_timeout_seconds": settings.stable_timeout_seconds,
            "watch_extensions": sorted(ext.lstrip(".") for ext in settings.watch_extensions),
        }

    def save_from_payload(self, payload: dict) -> dict:
        """校验并写入 upload.toml，立刻替换内存配置。"""
        text = render_upload_toml(payload)
        temp_path = self.config_path.with_suffix(".toml.tmp")
        temp_path.write_text(text, encoding="utf-8")
        try:
            loaded = load_upload_settings(temp_path, self.project_dir)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        # Docker 把单个文件 bind-mount 时，rename 到挂载点会 EBUSY
        try:
            temp_path.replace(self.config_path)
        except OSError:
            self.config_path.write_text(temp_path.read_text(encoding="utf-8"), encoding="utf-8")
            temp_path.unlink(missing_ok=True)
        self._settings = loaded
        self._mtime = self.config_path.stat().st_mtime
        logger.info(
            "已从 API 写入上传配置: chat_id=%s preview=%s after_success=%s concurrency=%s",
            loaded.chat_id,
            loaded.preview,
            loaded.after_success,
            loaded.concurrency,
        )
        return self.public_dict()

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
        # 重启后内存快照没了，只能退回当前配置；after_success 尚未落库
        need_preview = bool(row.get("single_page") or row.get("content_page"))
        return TaskPolicy(
            need_preview=need_preview,
            after_success=self._settings.after_success,
            max_retries=int(row.get("max_retries") or self._settings.max_retries),
        )

    def reload_if_changed(self) -> bool:
        """mtime 变了才重读。返回是否真的换了配置。

        解析失败保持上一份，避免前端/人工写成坏 toml 把服务弄死。
        """
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
