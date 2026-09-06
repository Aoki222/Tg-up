# config.py

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = BASE_DIR.parent
SESSION_DIR = PROJECT_DIR / "sessions"

from dotenv import load_dotenv


load_dotenv()


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"环境变量 {name} 未配置"
        )

    return value

def get_path_env(name: str, default: str) -> Path:
    value = os.getenv(name, default)

    path = Path(value)

    if not path.is_absolute():
        path = PROJECT_DIR / path

    return path



API_ID = int(get_required_env("API_ID"))
API_HASH = get_required_env("API_HASH")

BOT_TOKEN = os.getenv("BOT_TOKEN") or None

TARGET_CHAT_ID = int(
    get_required_env("TARGET_CHAT_ID")
)

SESSION_PATH = get_path_env(
    "SESSION_PATH",
    "sessions/session.session",
)

OBSERVER_PATH = get_path_env(
    "OBSERVER_PATH",
    str(PROJECT_DIR / "observer"),
)

UPLOAD_CONCURRENCY = int(
    os.getenv(
        "UPLOAD_CONCURRENCY",
        "3",
    )
)

UPLOAD_MAX_RETRIES = int(
    os.getenv(
        "UPLOAD_MAX_RETRIES",
        "3",
    )
)

UPLOAD_TIMEOUT = int(
    os.getenv(
        "UPLOAD_TIMEOUT",
        "1200",
    )
)

SESSION_FILES = {
    path.stem: path.with_suffix("")
    for path in SESSION_DIR.glob("*.session")
}