"""初始化表结构，并给旧库补列。

CREATE TABLE IF NOT EXISTS 不会改已有表。新列用 ALTER 加上，再把旧的
Telegram 列复制到 platform / dest_id / dest_extra / remote_id / assigned_worker。
复制不覆盖已有值，也不清空旧列。新写入只维护新列；chat_id 因 NOT NULL 仍写。
"""

from __future__ import annotations

from pathlib import Path

from ..logger import get_logger
from .connection import get_db, open_pool

logger = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT    NOT NULL UNIQUE,          -- UUID
    telegram_msg_id TEXT,                         -- 旧列，成功回执改记 remote_id
    file_path       TEXT    NOT NULL,
    file_name       TEXT    NOT NULL,
    folder_name     TEXT,
    file_size       INTEGER NOT NULL DEFAULT 0,
    chat_id         INTEGER NOT NULL,         -- 仅满足非空；发送以 dest_id 为准
    topic_id        INTEGER,                      -- 旧列，新任务不再写入，话题在 dest_extra
    caption         TEXT    DEFAULT '',
    
    single_page     INTEGER NOT NULL DEFAULT 0,
    content_page     INTEGER NOT NULL DEFAULT 0,
    page_path        TEXT    DEFAULT NULL,
    
    status          TEXT    NOT NULL DEFAULT 'pending',
    -- 现行：preparing / pending / assigned / uploading / success / failed / oversized
    -- retrying 仅兼容旧行，调度时当 pending 处理
    -- oversized 超过 Bot 上限，只在看板停留，不自动分配
    
    assigned_bot    TEXT,                         -- 旧列，认领改记 assigned_worker
    assigned_worker TEXT,                         -- 当前领任务的 worker 名
    platform        TEXT    NOT NULL DEFAULT 'telegram', -- telegram / gdrive
    dest_id         TEXT,                         -- 群 id 或 Drive folder id
    dest_extra      TEXT,                         -- Telegram 话题 id
    remote_id       TEXT,                         -- 成功后的消息 id 或 Drive file id
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    after_success   TEXT    NOT NULL DEFAULT 'keep',
    error_msg       TEXT,
    parent_id       INTEGER,
    part_index      INTEGER,
    part_count      INTEGER,
    slice_parts     INTEGER,
    
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    assigned_at     DATETIME,
    started_at      DATETIME,
    finished_at     DATETIME,
    deleted         INTEGER NOT NULL DEFAULT 0,
    
    CHECK(status IN ('preparing','pending','assigned','uploading','success','failed','retrying','oversized'))
);

CREATE INDEX IF NOT EXISTS idx_status          ON upload_tasks(status);
CREATE INDEX IF NOT EXISTS idx_status_size     ON upload_tasks(status, file_size);
CREATE INDEX IF NOT EXISTS idx_assigned_bot    ON upload_tasks(assigned_bot);
CREATE INDEX IF NOT EXISTS idx_assigned_status ON upload_tasks(assigned_bot, status);
CREATE INDEX IF NOT EXISTS idx_file_path_status ON upload_tasks(file_path, status);
CREATE INDEX IF NOT EXISTS idx_started_at      ON upload_tasks(started_at);
-- parent_id 的索引不能写在这里。旧库还没有这一列，CREATE INDEX 会在 ALTER 之前失败。
-- 列补上、表重建之后，由 _UPLOAD_TASK_INDEXES 再建。

CREATE TABLE IF NOT EXISTS upload_slices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id     INTEGER NOT NULL,
    part_index    INTEGER NOT NULL,
    part_count    INTEGER NOT NULL,
    segment_path  TEXT    NOT NULL,
    segment_size  INTEGER NOT NULL DEFAULT 0,
    child_task_id INTEGER,
    status        TEXT    NOT NULL DEFAULT 'planned',
    UNIQUE(parent_id, part_index)
);

CREATE INDEX IF NOT EXISTS idx_upload_slices_parent ON upload_slices(parent_id);

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

CREATE TABLE IF NOT EXISTS unmatched_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path     TEXT    NOT NULL UNIQUE,
    file_name     TEXT    NOT NULL,
    folder_name   TEXT,
    file_size     INTEGER NOT NULL DEFAULT 0,
    discovered_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_channels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_name TEXT    NOT NULL,
    chat_id      INTEGER NOT NULL,
    title        TEXT    NOT NULL DEFAULT '',
    type         TEXT    NOT NULL DEFAULT 'group',
    username     TEXT    NOT NULL DEFAULT '',
    is_active    INTEGER NOT NULL DEFAULT 1,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    synced_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_name, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_telegram_channels_active
    ON telegram_channels(account_name, is_active);
"""

_UPLOAD_TASK_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_status ON upload_tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_status_size ON upload_tasks(status, file_size)",
    "CREATE INDEX IF NOT EXISTS idx_assigned_bot ON upload_tasks(assigned_bot)",
    "CREATE INDEX IF NOT EXISTS idx_assigned_status ON upload_tasks(assigned_bot, status)",
    "CREATE INDEX IF NOT EXISTS idx_file_path_status ON upload_tasks(file_path, status)",
    "CREATE INDEX IF NOT EXISTS idx_started_at ON upload_tasks(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_status_platform ON upload_tasks(status, platform)",
    "CREATE INDEX IF NOT EXISTS idx_upload_tasks_parent ON upload_tasks(parent_id)",
)


def _fresh_upload_tasks_sql(table_name: str = "upload_tasks") -> str:
    """从当前 SCHEMA 取出建表语句。旧库的 sqlite_master 不含 ALTER 补上的列。"""
    start = SCHEMA.index("CREATE TABLE IF NOT EXISTS upload_tasks")
    end = SCHEMA.index("CREATE INDEX IF NOT EXISTS idx_status")
    statement = SCHEMA[start:end].strip()
    if statement.endswith(";"):
        statement = statement[:-1].strip()
    statement = statement.replace("CREATE TABLE IF NOT EXISTS upload_tasks", f"CREATE TABLE {table_name}", 1)
    return statement


async def init_db() -> None:
    await open_pool()
    async with get_db() as db:
        await db.executescript(SCHEMA)
        await _ensure_column(
            db,
            "upload_tasks",
            "after_success",
            "TEXT NOT NULL DEFAULT 'keep'",
        )
        await _ensure_column(db, "upload_tasks", "assigned_worker", "TEXT")
        await _ensure_column(db, "upload_tasks", "platform", "TEXT NOT NULL DEFAULT 'telegram'")
        await _ensure_column(db, "upload_tasks", "dest_id", "TEXT")
        await _ensure_column(db, "upload_tasks", "dest_extra", "TEXT")
        await _ensure_column(db, "upload_tasks", "remote_id", "TEXT")
        await _ensure_column(db, "upload_tasks", "parent_id", "INTEGER")
        await _ensure_column(db, "upload_tasks", "part_index", "INTEGER")
        await _ensure_column(db, "upload_tasks", "part_count", "INTEGER")
        await _ensure_column(db, "upload_tasks", "slice_parts", "INTEGER")
        await _backfill_destination_columns(db)
        await _allow_oversized_status(db)
        await _ensure_upload_task_indexes(db)
        await _park_existing_oversized(db)
        await db.commit()
    logger.info("数据库初始化完成")


async def _ensure_column(db, table: str, column: str, ddl: str) -> None:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        names = {row[1] for row in await cursor.fetchall()}
    if column not in names:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.info("已为 %s 增加列 %s", table, column)


async def _allow_oversized_status(db) -> None:
    """旧库的 CHECK 没有 oversized。ALTER 改不了 CHECK，只能重建表。

    先写入 upload_tasks_new，复制成功后再丢掉旧表。中途失败时旧表还在。
    """
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'upload_tasks_new'"
    ) as cursor:
        has_new = await cursor.fetchone() is not None
    async with db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'upload_tasks'"
    ) as cursor:
        current = await cursor.fetchone()

    if has_new and current is None:
        await db.execute("ALTER TABLE upload_tasks_new RENAME TO upload_tasks")
        await _sync_upload_tasks_sequence(db)
        logger.info("已接上中断的 upload_tasks 重建")
        return
    if has_new and current is not None:
        await db.execute("DROP TABLE upload_tasks_new")

    if current is None:
        return
    ddl = current[0] or ""
    if "oversized" in ddl:
        return

    logger.info("重建 upload_tasks，允许 oversized 状态")
    await db.execute(_fresh_upload_tasks_sql("upload_tasks_new"))
    async with db.execute("PRAGMA table_info(upload_tasks)") as cursor:
        legacy_cols = [item[1] for item in await cursor.fetchall()]
    async with db.execute("PRAGMA table_info(upload_tasks_new)") as cursor:
        new_cols = [item[1] for item in await cursor.fetchall()]
    shared = [name for name in new_cols if name in set(legacy_cols)]
    if not shared:
        raise RuntimeError("重建 upload_tasks 失败：没有可复制的列")
    columns = ", ".join(shared)
    await db.execute(
        f"INSERT INTO upload_tasks_new ({columns}) SELECT {columns} FROM upload_tasks"
    )
    await db.execute("DROP TABLE upload_tasks")
    await db.execute("ALTER TABLE upload_tasks_new RENAME TO upload_tasks")
    await _sync_upload_tasks_sequence(db)


async def _ensure_upload_task_indexes(db) -> None:
    for statement in _UPLOAD_TASK_INDEXES:
        await db.execute(statement)


async def _sync_upload_tasks_sequence(db) -> None:
    """重建后按现有最大 id 对齐自增，避免下一条任务撞主键。"""
    async with db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ) as cursor:
        if await cursor.fetchone() is None:
            return
    await db.execute(
        "DELETE FROM sqlite_sequence WHERE name IN ('upload_tasks', 'upload_tasks_new', 'upload_tasks_legacy')"
    )
    await db.execute(
        """INSERT INTO sqlite_sequence (name, seq)
           SELECT 'upload_tasks', COALESCE(MAX(id), 0) FROM upload_tasks"""
    )


async def _park_existing_oversized(db) -> int:
    """启动时把已经超过 Bot 上限的待传/失败/在途任务拉出自动调度。"""
    from ..domain import limits

    cursor = await db.execute(
        """UPDATE upload_tasks
           SET status = 'oversized',
               assigned_worker = NULL,
               assigned_bot = NULL,
               assigned_at = NULL,
               started_at = NULL,
               error_msg = CASE
                   WHEN error_msg IS NULL OR TRIM(error_msg) = '' THEN ?
                   ELSE error_msg
               END
           WHERE status IN ('pending', 'retrying', 'failed', 'assigned', 'uploading')
             AND file_size > ?
             AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'""",
        (limits.OVERSIZED_REASON, limits.TELEGRAM_BOT_MAX_BYTES),
    )
    count = int(cursor.rowcount or 0)
    if count:
        logger.info("超过 2GB 的任务已改为过大，不自动分配: %s", count)
    return count


async def _backfill_destination_columns(db) -> None:
    """旧行没有通用目的地列时，用 Telegram 列补上。已有值不覆盖。"""
    await db.execute(
        """UPDATE upload_tasks
           SET platform = 'telegram'
           WHERE platform IS NULL OR platform = ''"""
    )
    await db.execute(
        """UPDATE upload_tasks
           SET dest_id = CAST(chat_id AS TEXT)
           WHERE (dest_id IS NULL OR dest_id = '') AND chat_id IS NOT NULL"""
    )
    await db.execute(
        """UPDATE upload_tasks
           SET dest_extra = CAST(topic_id AS TEXT)
           WHERE (dest_extra IS NULL OR dest_extra = '') AND topic_id IS NOT NULL"""
    )
    await db.execute(
        """UPDATE upload_tasks
           SET remote_id = telegram_msg_id
           WHERE (remote_id IS NULL OR remote_id = '') AND telegram_msg_id IS NOT NULL"""
    )
    await db.execute(
        """UPDATE upload_tasks
           SET assigned_worker = assigned_bot
           WHERE (assigned_worker IS NULL OR assigned_worker = '') AND assigned_bot IS NOT NULL"""
    )
    