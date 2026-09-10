import os
import uuid

from .connection import get_db

async def insert_single_task(
    file_path: str,
    file_name: str,
    file_size: int,
    folder_name: str,
    max_retries: int = 3,
) -> tuple[int, int]:

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO upload_tasks (
                task_id,
                file_path,
                file_name,
                folder_name,
                file_size,
                chat_id,
                caption,
                max_retries
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                file_path,
                file_name,
                folder_name,
                file_size,
                int(os.getenv("TARGET_CHAT_ID", "0")),
                "",
                max_retries,
            ),
        )
        await db.commit()
        
        async with db.execute(
            """
            SELECT
                (SELECT COUNT(*)
                FROM upload_tasks
                WHERE file_name = ?) AS file_name_count,

                (SELECT COUNT(*)
                FROM upload_tasks) AS total_count
            """,
            (file_name,),
        ) as cursor:
            row = await cursor.fetchone()

        file_name_count = row[0]
        total_count = row[1]        

        return total_count, file_name_count