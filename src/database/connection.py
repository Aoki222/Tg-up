"""每次操作打开一个 SQLite 连接，用完关闭。

row_factory=Row 以便 dict(row)。目前未开 WAL，并发高时可能遇到 database is locked。
"""

from pathlib import Path
from contextlib import asynccontextmanager
import aiosqlite

from ..logger import get_logger
from ..config import DATABASE_PATH

logger = get_logger(__name__)


@asynccontextmanager
async def get_db():
    """打开一次连接，yield 给调用方，退出时关闭。"""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        await db.execute("PRAGMA foreign_keys = ON")

        yield db