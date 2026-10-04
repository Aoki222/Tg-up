"""系统配置，import 时读一次 .env。

放这里的：数据库路径、session 目录、控制台监听地址。
监听地址和数据库路径改完要重启。

Telegram API_ID / API_HASH 不在这里卡住启动。运行时以 data/telegram.json 为准，
文件不完整才回落到下面这两个环境变量快照。详见 domain.telegram_credentials。

不要把凭据放进 upload.toml。目标群、封面模式、删不删文件由 SettingsHub 热更新。
sessions 目录只提供路径，里面的 *.session 由 SessionPool 运行中反复扫描。
"""

import os
import threading
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = BASE_DIR.parent
SESSION_DIR = PROJECT_DIR / "sessions"
DATABASE_PATH = PROJECT_DIR / "data" / "app.db"
PAGE_DIR = PROJECT_DIR / "page"

ENV_PATH = PROJECT_DIR / ".env"
load_dotenv(ENV_PATH)


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"环境变量 {name} 未配置")
    return value


def _env_api_id() -> int:
    raw = (os.getenv("API_ID") or "").strip()
    if not raw:
        return 0
    try:
        parsed = int(raw)
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


# import 时的环境变量快照。网页保存的凭据在 data/telegram.json，不回写这两个名字。
API_ID = _env_api_id()
API_HASH = (os.getenv("API_HASH") or "").strip()

# 进度页 / SSE。Vue 开发时也可跨域打这个地址
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
# 为空则不校验。多容器同机调用时设置，请求带 Authorization: Bearer <token>
API_TOKEN = os.getenv("API_TOKEN") or None
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY") or None


def mask_api_hash(value: str) -> str:
    text = (value or "").strip()
    if len(text) <= 6:
        return "••••"
    return f"{text[:4]}••••{text[-2:]}"


_dotenv_lock = threading.Lock()


def upsert_dotenv(updates: dict[str, str], path: Path | None = None) -> None:
    """只改给定键，其它行和注释原样保留。"""
    with _dotenv_lock:
        target = path or ENV_PATH
        lines: list[str] = []
        if target.exists():
            lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        seen: set[str] = set()
        rewritten: list[str] = []
        for line in lines:
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                rewritten.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                ending = "\n" if line.endswith("\n") else ""
                rewritten.append(f"{key}={updates[key]}{ending}")
                seen.add(key)
            else:
                rewritten.append(line)

        if rewritten and not rewritten[-1].endswith("\n"):
            rewritten.append("\n")
        for key, value in updates.items():
            if key not in seen:
                rewritten.append(f"{key}={value}\n")
        target.write_text("".join(rewritten), encoding="utf-8")
