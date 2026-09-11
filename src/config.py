"""系统配置（进程身份）。

这里的值在 import 时读一次：API_ID / API_HASH、数据库路径、session 目录。
改这些必须重启。上传策略（目标群、是否删文件、并发）在 upload.toml，由 SettingsHub 热更新。
sessions 目录只提供路径，文件列表由 SessionPool 运行中反复扫描。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = BASE_DIR.parent
SESSION_DIR = PROJECT_DIR / "sessions"
DATABASE_PATH = PROJECT_DIR / "data" / "app.db"
PAGE_DIR = PROJECT_DIR / "page"

load_dotenv()


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"环境变量 {name} 未配置")
    return value


API_ID = int(get_required_env("API_ID"))
API_HASH = get_required_env("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN") or None
