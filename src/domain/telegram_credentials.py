"""Telegram API 凭据。

完整的 api_id + api_hash 优先读 data/telegram.json。
文件不存在或不完整时用环境变量 API_ID / API_HASH。
两边都没有则视为未配置：进程照常启动，控制台可以先打开。

这份文件和 upload.toml 分开。设置页整份重写 upload.toml 时不会碰到这里，
接口也不会把 api_hash 放进公开配置。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def credentials_path(project_dir: Path) -> Path:
    return project_dir / "data" / "telegram.json"


def credentials_ready(api_id: int, api_hash: str) -> bool:
    return int(api_id) > 0 and bool((api_hash or "").strip())


@dataclass(frozen=True)
class TelegramCredentials:
    api_id: int
    api_hash: str
    source: str

    @property
    def configured(self) -> bool:
        return credentials_ready(self.api_id, self.api_hash)


def _parse_api_id(value: object) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _read_file(path: Path) -> TelegramCredentials | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    api_hash = raw.get("api_hash")
    text = api_hash.strip() if isinstance(api_hash, str) else ""
    return TelegramCredentials(_parse_api_id(raw.get("api_id")), text, "file")


def _from_environ(environ: Mapping[str, str]) -> TelegramCredentials:
    api_hash = environ.get("API_HASH") or ""
    return TelegramCredentials(_parse_api_id(environ.get("API_ID")), api_hash.strip(), "env")


def load_telegram_credentials(
    project_dir: Path,
    environ: Mapping[str, str] | None = None,
) -> TelegramCredentials:
    """文件完整则用文件，否则用环境变量，否则 source 为 none。"""
    file_creds = _read_file(credentials_path(project_dir))
    if file_creds is not None and file_creds.configured:
        return file_creds
    env_creds = _from_environ(os.environ if environ is None else environ)
    if env_creds.configured:
        return env_creds
    return TelegramCredentials(0, "", "none")


def save_telegram_credentials(project_dir: Path, api_id: int, api_hash: str) -> TelegramCredentials:
    """原子写入 data/telegram.json。只保存 api_id 和 api_hash。"""
    path = credentials_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"api_id": int(api_id), "api_hash": (api_hash or "").strip()}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return TelegramCredentials(payload["api_id"], payload["api_hash"], "file")


def merge_submitted_hash(submitted: str, current_hash: str) -> str:
    """空串或带掩码圆点时保留当前 hash。结果仍不足 16 位则拒绝。"""
    text = (submitted or "").strip()
    if not text or "•" in text:
        text = (current_hash or "").strip()
    if len(text) < 16:
        raise ValueError("API_HASH 长度不足")
    return text
