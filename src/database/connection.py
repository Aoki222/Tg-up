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
    """每次 SQL 打开一个连接，用完关闭。row_factory=Row 以便 dict(row)。

    未开 WAL；并发高时 Windows 上可能 database is locked。
    """
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 即使当前 schema 几乎不用外键，打开以免以后加表漏掉
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA busy_timeout = 5000")

        yield db