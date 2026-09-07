import uuid
from ..database.connection import get_db
from ..config import TARGET_CHAT_ID

class TaskRepository:
    """所有 upload_tasks 表操作收口在此。"""

    async def add_task(self, file_path: str, file_name: str, file_size: int, folder_name: str, max_retries: int = 3) -> None:
        async with get_db() as database:
            await database.execute(
                """INSERT INTO upload_tasks
                   (task_id, file_path, file_name, folder_name, file_size, chat_id, caption, max_retries)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), file_path, file_name, folder_name, file_size, TARGET_CHAT_ID, "", max_retries),
            )
            await database.commit()

    async def count_active_tasks(self, worker_name: str) -> int:
        async with get_db() as database:
            async with database.execute(
                "SELECT COUNT(*) FROM upload_tasks WHERE assigned_bot = ? AND status IN ('assigned', 'uploading')",
                (worker_name,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0]

    async def fetch_pending_tasks(self, limit: int) -> list[dict]:
        async with get_db() as database:
            async with database.execute(
                """SELECT * FROM upload_tasks
                   WHERE status IN ('pending', 'retrying')
                   ORDER BY file_size ASC LIMIT ?""",
                (limit,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def claim_task(self, task_id: int, worker_name: str) -> bool:
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
                "UPDATE upload_tasks SET status = 'uploading', started_at = CURRENT_TIMESTAMP WHERE id = ?",
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
        new_status = "failed" if retry_count >= max_retries else "pending"
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = ?, retry_count = ?, error_msg = ?,
                   assigned_bot = NULL, assigned_at = NULL, started_at = NULL WHERE id = ?""",
                (new_status, retry_count, error_message[:500], task_id),
            )
            await database.commit()

    async def recover_timed_out_tasks(self) -> int:
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET status = 'pending', assigned_bot = NULL,
                   assigned_at = NULL, started_at = NULL, error_msg = 'timeout recovered'
                   WHERE status = 'uploading' AND started_at < datetime('now', '-2 hours')"""
            )
            await database.commit()
            return cursor.rowcount