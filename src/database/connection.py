from pathlib import Path
from contextlib import asynccontextmanager
import aiosqlite

from ..logger import get_logger
from ..config import DATABASE_PATH

logger = get_logger(__name__)


@asynccontextmanager
async def get_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        await db.execute("PRAGMA foreign_keys = ON")

        yield db