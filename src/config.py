# 系统配置：进程身份。改这些需要重启。上传策略在 upload.toml。

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

SESSION_FILES = {
    path.stem: path.with_suffix("")
    for path in SESSION_DIR.glob("*.session")
}
