"""系统配置（进程身份），import 时读一次 .env。

放这里的：API_ID / API_HASH、数据库路径、session 目录、控制台监听地址。
改这些必须重启进程。

不要放目标群、封面模式、删不删文件——那些在 upload.toml，由 SettingsHub 热更新。
sessions 目录只提供路径，里面的 *.session 由 SessionPool 运行中反复扫描。
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

# 进度页 / SSE。Vue 开发时也可跨域打这个地址
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
# 为空则不校验。多容器同机调用时设置，请求带 Authorization: Bearer <token>
API_TOKEN = os.getenv("API_TOKEN") or None
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY") or None
