"""upload_tasks / chat_topic 的唯一写入口。

返回 dict 行，由调用方装配成领域 Task。
认领、回 pending、标成功都必须带状态条件或清掉 assigned_bot，
否则崩溃后会出现「库里占着槽、内存里没任务」的假忙。
"""

import uuid

from ..database.connection import get_db


class TaskRepository:
    """upload_tasks 持久化。返回行字典，领域 Task 由调用方装配。"""

    async def add_task(
        self,
        file_path: str,
        file_name: str,
        file_size: int,
        folder_name: str,
        chat_id: int,
        single_page: bool = False,
        content_page: bool = False,
        status: str = "pending",
        max_retries: int = 3,
        topic_id: int | None = None,
    ) -> int:
        """插入一条任务。single_page/content_page 是封面需求快照，后续只读。"""
        async with get_db() as database:
            cursor = await database.execute(
                """INSERT INTO upload_tasks
                (
                   task_id,
                   file_path,
                   file_name,
                   folder_name,
                   file_size,
                   chat_id,
                   topic_id,
                   caption,
                   single_page,
                   content_page,
                   page_path,
                   status,
                   max_retries
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    file_path,
                    file_name,
                    folder_name,
                    file_size,
                    chat_id,
                    topic_id,
                    "",
                    int(single_page),
                    int(content_page),
                    status,
                    max_retries,
                ),
            )
            await database.commit()
            task_id = cursor.lastrowid
            if task_id is None:
                raise RuntimeError("入库失败：未返回任务 id")
            return int(task_id)

    async def find_active_by_file_path(self, file_path: str) -> int | None:
        """未完成任务按绝对路径去重。success/failed 的同路径允许再来一条。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT id FROM upload_tasks
                   WHERE file_path = ? AND status NOT IN ('success', 'failed')
                   LIMIT 1""",
                (file_path,),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else None

    async def get_chat_topic(self, chat_id: int, topic_path: str) -> int | None:
        async with get_db() as database:
            async with database.execute(
                "SELECT topic_id FROM chat_topic WHERE chat_id = ? AND topic_path = ?",
                (chat_id, topic_path),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else None

    async def save_chat_topic(self, chat_id: int, topic_id: int, topic_path: str) -> None:
        async with get_db() as database:
            await database.execute(
                """INSERT INTO chat_topic (chat_id, topic_id, topic_path)
                   VALUES (?, ?, ?)
                   ON CONFLICT(chat_id, topic_path) DO UPDATE SET
                       topic_id = excluded.topic_id,
                       updated_at = CURRENT_TIMESTAMP""",
                (chat_id, topic_id, topic_path),
            )
            await database.commit()

    async def count_active_tasks(self, worker_name: str) -> int:
        """assigned + uploading 都占槽。只数内存队列会在崩溃后低估负载。"""
        async with get_db() as database:
            async with database.execute(
                "SELECT COUNT(*) FROM upload_tasks WHERE assigned_bot = ? AND status IN ('assigned', 'uploading')",
                (worker_name,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0]

    async def fetch_pending_tasks(self, limit: int) -> list[dict]:
        """小文件优先。含旧数据里可能残留的 retrying。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT * FROM upload_tasks
                   WHERE status IN ('pending', 'retrying')
                   ORDER BY file_size ASC LIMIT ?""",
                (limit,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def claim_task(self, task_id: int, worker_name: str) -> bool:
        # 带 status 条件的 CAS：抢不到说明已被别的调度轮次领走
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET status = 'assigned', assigned_bot = ?, assigned_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status IN ('pending', 'retrying')""",
                (worker_name, task_id),
            )
            await database.commit()
            return cursor.rowcount > 0

    async def mark_task_uploading(self, task_id: int) -> None:
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = 'uploading', started_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status = 'assigned'""",
                (task_id,),
            )
            await database.commit()

    async def mark_task_succeeded(self, task_id: int, telegram_message_id: int) -> None:
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = 'success', finished_at = CURRENT_TIMESTAMP,
                   telegram_msg_id = ? WHERE id = ?""",
                (telegram_message_id, task_id),
            )
            await database.commit()

    async def mark_task_failed(self, task_id: int, retry_count: int, max_retries: int, error_message: str) -> None:
        """未超限回 pending 并清空归属，让别的 worker 可以再抢。"""
        new_status = "failed" if retry_count >= max_retries else "pending"
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = ?, retry_count = ?, error_msg = ?,
                   assigned_bot = NULL, assigned_at = NULL, started_at = NULL WHERE id = ?""",
                (new_status, retry_count, error_message[:500], task_id),
            )
            await database.commit()

    async def release_task(self, task_id: int, error_message: str) -> None:
        """回 pending 且不增加 retry_count。FloodWait 走这条。"""
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = 'pending', error_msg = ?,
                   assigned_bot = NULL, assigned_at = NULL, started_at = NULL WHERE id = ?""",
                (error_message[:500], task_id),
            )
            await database.commit()

    async def reconcile_stale_tasks(self) -> int:
        """进程刚起来时内存队列是空的，这三种状态都是幽灵任务。"""
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET status = 'pending', assigned_bot = NULL,
                   assigned_at = NULL, started_at = NULL, error_msg = 'recovered on startup'
                   WHERE status IN ('assigned', 'uploading', 'preparing')"""
            )
            await database.commit()
            return cursor.rowcount

    async def recover_timed_out_tasks(
        self,
        uploading_timeout_seconds: int = 1200,
        assigned_timeout_seconds: int = 600,
    ) -> int:
        """运行中兜底：uploading / assigned 超时都打回 pending。"""
        async with get_db() as database:
            uploading = await database.execute(
                """UPDATE upload_tasks SET status = 'pending', assigned_bot = NULL,
                   assigned_at = NULL, started_at = NULL, error_msg = 'timeout recovered'
                   WHERE status = 'uploading'
                     AND started_at < datetime('now', ?)""",
                (f"-{uploading_timeout_seconds} seconds",),
            )
            assigned = await database.execute(
                """UPDATE upload_tasks SET status = 'pending', assigned_bot = NULL,
                   assigned_at = NULL, started_at = NULL, error_msg = 'assigned timeout recovered'
                   WHERE status = 'assigned'
                     AND assigned_at < datetime('now', ?)""",
                (f"-{assigned_timeout_seconds} seconds",),
            )
            await database.commit()
            return uploading.rowcount + assigned.rowcount

    async def update_preview(self, task_id: int, page_path: str | None, success: bool, error_message: str = "") -> None:
        """截图结束的唯一放行点：无论成败都转到 pending，让调度器能看见。"""
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET page_path = ?, status = 'pending',
                   error_msg = ? WHERE id = ?""",
                (page_path, ("" if success else f"preview failed: {error_message}"[:500]), task_id),
            )
            await database.commit()

    async def get_task_by_id(self, task_id: int) -> dict | None:
        async with get_db() as database:
            async with database.execute(
                "SELECT * FROM upload_tasks WHERE id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
