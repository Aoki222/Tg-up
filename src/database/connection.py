"""每次操作打开一个 SQLite 连接，用完关闭。

WAL + busy_timeout，调度/Worker/ingest/预览可以并发写。
"""

from pathlib import Path
from contextlib import asynccontextmanager
import aiosqlite

from ..logger import get_logger
from ..config import DATABASE_PATH

logger = get_logger(__name__)


@asynccontextmanager
async def get_db():
    """每次 SQL 打开一个连接，用完关闭。row_factory=Row 以便 dict(row)。"""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA busy_timeout = 5000")
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        yield db