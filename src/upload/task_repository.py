import uuid
from ..database.connection import get_db
from ..config import TARGET_CHAT_ID

class TaskRepository:
    """所有 upload_tasks 表操作收口在此。"""

    async def add_task(self, file_path: str, file_name: str, file_size: int, folder_name: str, 
                       single_page: bool = False, content_page: bool = False, chat_id: int = TARGET_CHAT_ID,
                       status: str = "pending", max_retries: int = 3) -> int:
        # 快照列建任务：截图需求一次写死，后续只读；有图则 preparing 暂不可见
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
                   caption,
                   single_page,
                   content_page,
                   page_path,
                   status,
                   max_retries
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (str(uuid.uuid4()), file_path, file_name, folder_name, file_size, chat_id, "",
                 int(single_page), int(content_page), status, max_retries),
            )
            await database.commit()
            return cursor.lastrowid

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
        # 原子抢占：只有 pending/retrying 才能被认领，防多调度器重复分发
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
        # 失败回 pending 供下轮重抢，超重试次数才落 failed；清掉归属避免脏占用
        new_status = "failed" if retry_count >= max_retries else "pending"
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = ?, retry_count = ?, error_msg = ?,
                   assigned_bot = NULL, assigned_at = NULL, started_at = NULL WHERE id = ?""",
                (new_status, retry_count, error_message[:500], task_id),
            )
            await database.commit()

    async def recover_timed_out_tasks(self) -> int:
        # 兜底回收：uploading 超 2 小时视为掉线/被 kill，回 pending 下次重传
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET status = 'pending', assigned_bot = NULL,
                   assigned_at = NULL, started_at = NULL, error_msg = 'timeout recovered'
                   WHERE status = 'uploading' AND started_at < datetime('now', '-2 hours')"""
            )
            await database.commit()
            return cursor.rowcount

    async def update_preview(self, task_id: int, page_path: str | None,
                             success: bool, error_message: str = "") -> None:
        # 唯一放行点：成功写主图转 pending，失败保留快照置空转 pending 只发视频
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET page_path = ?, status = 'pending',
                   error_msg = ? WHERE id = ?""",
                (page_path, ("" if success else f"preview failed: {error_message}"[:500]), task_id),
            )
            await database.commit()

    async def get_task_by_id(self, task_id: int) -> dict | None:
        # Worker 开工前重读最新行，拿到回填后的 page_path
        async with get_db() as database:
            async with database.execute(
                "SELECT * FROM upload_tasks WHERE id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None