"""初始化表结构。

CREATE TABLE IF NOT EXISTS 不会给已经存在的旧库加新列。
当前约定先不跑迁移：新库用这份 schema，旧库缺列时需要人工处理。
"""

from __future__ import annotations

from pathlib import Path

from ..logger import get_logger
from .connection import get_db

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
    topic_id        INTEGER,
    caption         TEXT    DEFAULT '',
    
    single_page     INTEGER NOT NULL DEFAULT 0,
    content_page     INTEGER NOT NULL DEFAULT 0,
    page_path        TEXT    DEFAULT NULL,
    
    status          TEXT    NOT NULL DEFAULT 'pending',
    -- 现行：preparing / pending / assigned / uploading / success / failed
    -- retrying 仅兼容旧行，调度时当 pending 处理
    
    assigned_bot    TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    error_msg       TEXT,
    
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    assigned_at     DATETIME,
    started_at      DATETIME,
    finished_at     DATETIME,
    deleted         INTEGER NOT NULL DEFAULT 0,
    
    CHECK(status IN ('preparing','pending','assigned','uploading','success','failed','retrying'))
);

CREATE INDEX IF NOT EXISTS idx_status          ON upload_tasks(status);
CREATE INDEX IF NOT EXISTS idx_status_size     ON upload_tasks(status, file_size);
CREATE INDEX IF NOT EXISTS idx_assigned_bot    ON upload_tasks(assigned_bot);
CREATE INDEX IF NOT EXISTS idx_started_at      ON upload_tasks(started_at);

CREATE TABLE IF NOT EXISTS chat_topic (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    topic_id    INTEGER NOT NULL,
    topic_path  TEXT    NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, topic_path)
);

CREATE INDEX IF NOT EXISTS idx_chat_topic_chat_path
    ON chat_topic(chat_id, topic_path);
"""


async def init_db() -> None:
    """只 CREATE IF NOT EXISTS，不会给已有表加列。"""

    async with get_db() as db:
        await db.executescript(SCHEMA)
        
        await db.commit()

    logger.info("数据库初始化完成")
    