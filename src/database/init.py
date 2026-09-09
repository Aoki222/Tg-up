"""建表逻辑：定义 SQL schema 并初始化数据库。"""

from __future__ import annotations

from pathlib import Path

from ..logger import get_logger
from .connection import get_db
from .migrations import migrate_upload_tasks

logger = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT    NOT NULL UNIQUE,          -- UUID
    telegram_msg_id TEXT,
    file_path       TEXT    NOT NULL,
    file_name       TEXT    NOT NULL,
    folder_name     TEXT,
    file_size       INTEGER NOT NULL DEFAULT 0,
    chat_id         INTEGER NOT NULL,
    caption         TEXT    DEFAULT '',
    
    single_page     INTEGER NOT NULL DEFAULT 0,
    content_page     INTEGER NOT NULL DEFAULT 0,
    page_path        TEXT    DEFAULT NULL,
    
    status          TEXT    NOT NULL DEFAULT 'pending',
    -- pending / assigned / uploading / success / failed / retrying / preparing
    
    assigned_bot    TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    error_msg       TEXT,
    
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    assigned_at     DATETIME,
    started_at      DATETIME,
    finished_at     DATETIME,
    deleted         INTEGER NOT NULL DEFAULT 0,
    
    CHECK(status IN ('pending','assigned','uploading','success','failed','retrying', 'preparing'))
);

CREATE INDEX IF NOT EXISTS idx_status          ON upload_tasks(status);
CREATE INDEX IF NOT EXISTS idx_status_size     ON upload_tasks(status, file_size);
CREATE INDEX IF NOT EXISTS idx_assigned_bot    ON upload_tasks(assigned_bot);
CREATE INDEX IF NOT EXISTS idx_started_at      ON upload_tasks(started_at);
"""


async def init_db() -> None:
    """初始化数据库，创建必要的表和索引。"""

    async with get_db() as db:
        await db.executescript(SCHEMA)
        
        await db.commit()

    logger.info("数据库初始化完成")
    