"""进程内 SQLite 小池：启动打开，用完归还，退出关掉。

WAL + busy_timeout。池固定 4 条长连接，不是每次 SQL 新建，也不是大连接池。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from ..config import DATABASE_PATH
from ..logger import get_logger

logger = get_logger(__name__)

POOL_SIZE = 4

_pool: asyncio.Queue[aiosqlite.Connection] | None = None
_connections: list[aiosqlite.Connection] = []
_pool_path: Path | None = None
_open_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _open_lock
    if _open_lock is None:
        _open_lock = asyncio.Lock()
    return _open_lock


async def _configure(db: aiosqlite.Connection) -> None:
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA synchronous = NORMAL")


async def open_pool(path: Path | None = None) -> None:
    """打开固定数量的长连接。同一路径重复调用是空操作。"""
    global _pool, _connections, _pool_path
    db_path = Path(path or DATABASE_PATH)
    async with _lock():
        if _pool is not None and _pool_path == db_path:
            return
        if _pool is not None:
            await _close_pool_locked()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        connections: list[aiosqlite.Connection] = []
        for _ in range(POOL_SIZE):
            db = await aiosqlite.connect(db_path)
            await _configure(db)
            connections.append(db)
            await pool.put(db)
        _connections = connections
        _pool = pool
        _pool_path = db_path
        logger.info("SQLite 连接池已打开 size=%s path=%s", POOL_SIZE, db_path)


async def _close_pool_locked() -> None:
    global _pool, _connections, _pool_path
    connections = list(_connections)
    _pool = None
    _connections = []
    _pool_path = None
    for db in connections:
        try:
            await db.close()
        except Exception:
            logger.exception("关闭 SQLite 连接失败")


async def close_pool() -> None:
    async with _lock():
        await _close_pool_locked()


@asynccontextmanager
async def get_db():
    """从池里借一条连接，用完归还。尚未 open 时自动按默认路径打开。"""
    if _pool is None:
        await open_pool()
    pool = _pool
    if pool is None:
        raise RuntimeError("SQLite 连接池未打开")
    db = await pool.get()
    try:
        yield db
    finally:
        await pool.put(db)
