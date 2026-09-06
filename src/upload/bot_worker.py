import asyncio
import os
from telethon.errors import FloodWaitError

from ..database.connection import get_db
from ..logger import get_logger

logger = get_logger(__name__)

class BotWorker:
    def __init__(self, name, client, scheduler, max_concurrent=3):
        self.name = name
        self.client = client                # Telethon Client
        self.scheduler = scheduler
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.queue = asyncio.Queue()
        self._running = False

    async def start(self):
        self._running = True
        asyncio.create_task(self._consume_loop())
        logger.info("[%s] 上传 Worker 已启动，并发数：%s", self.name, self.semaphore._value)

    async def put_task(self, task: dict):
        await self.queue.put(task)
        logger.info("[%s] 任务已加入 Worker 队列：任务ID=%s，文件=%s", self.name, task.get("id"), task.get("file_name"))

    async def _consume_loop(self):
        while self._running:
            task = await self.queue.get()
            logger.info("[%s] 开始处理任务：任务ID=%s，文件=%s", self.name, task.get("id"), task.get("file_name"))
            asyncio.create_task(self._process_task(task))

    async def _process_task(self, task: dict):
        async with self.semaphore:          # 控制本 Bot 并发数
            task_id = task['id']
            file_path = task['file_path']

            # 标记开始上传
            async with get_db() as db:
                await db.execute("""
                UPDATE upload_tasks
                SET status = 'uploading',
                    started_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (task_id,))
                await db.commit()
            logger.info("[%s] 任务状态已更新为 uploading：任务ID=%s", self.name, task_id)

            try:
                msg = await self.client.send_file(
                    task['chat_id'],
                    file_path,
                    caption=task.get('caption') or '',
                )

                # ---------- 成功 ----------
                async with get_db() as db:
                    await db.execute("""
                    UPDATE upload_tasks
                    SET status = 'success',
                        finished_at = CURRENT_TIMESTAMP,
                        telegram_msg_id = ?
                    WHERE id = ?
                """, (msg.id, task_id))
                    await db.commit()

                # 删除本地文件
                if os.path.exists(file_path):
                    os.remove(file_path)
                logger.info("[%s] 上传成功并删除本地文件：任务ID=%s，文件=%s，消息ID=%s", self.name, task_id, file_path, msg.id)
                
            except FloodWaitError as e:
                logger.warning("[%s] 触发 Telegram 限流：任务ID=%s，需要等待%s秒", self.name, task_id, e.seconds)
                await self._handle_failure(task, f"FloodWait {e.seconds}s")
                await asyncio.sleep(e.seconds)

            except Exception as e:
                logger.exception("[%s] 上传失败：任务ID=%s，文件=%s", self.name, task_id, file_path)
                await self._handle_failure(task, str(e))

            finally:
                logger.info("[%s] 任务处理结束：任务ID=%s", self.name, task_id)
                self.scheduler.notify()

    async def _handle_failure(self, task, error_msg):
        new_retry = task.get('retry_count', 0) + 1
        max_retries = task.get('max_retries', 3)

        if new_retry >= max_retries:
            new_status = 'failed'
        else:
            new_status = 'pending'          # 重新回到可调度状态

        async with get_db() as db:
            await db.execute("""
            UPDATE upload_tasks
            SET status = ?,
                retry_count = ?,
                error_msg = ?,
                assigned_bot = NULL,
                assigned_at = NULL,
                started_at = NULL
            WHERE id = ?
        """, (new_status, new_retry, error_msg[:500], task['id']))
            await db.commit()

        logger.warning(
            "[%s] 任务失败，状态=%s，重试次数=%s/%s：任务ID=%s，原因=%s",
            self.name,
            new_status,
            new_retry,
            max_retries,
            task["id"],
            error_msg[:200],
        )