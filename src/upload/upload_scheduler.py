import asyncio

from ..logger import get_logger
from ..database.connection import get_db


logger = get_logger(__name__)


class UploadScheduler:
    def __init__(self, workers: dict, max_per_bot=3):
        self.workers = workers              # {name: BotWorker}
        self.max_per_bot = max_per_bot
        self.wakeup = asyncio.Event()
        self._running = False

    def notify(self):
        """任何地方状态变化时调用，唤醒调度器"""
        self.wakeup.set()
        logger.debug("调度器收到唤醒通知")

    async def start(self):
        self._running = True
        logger.info("上传调度器已启动：Worker数量=%s，每个Worker最大任务数=%s", len(self.workers), self.max_per_bot)
        self.notify()                       # 启动时立刻跑一次
        asyncio.create_task(self._timeout_checker())
        await self._run_loop()

    async def stop(self):
        self._running = False
        logger.info("上传调度器正在停止")
        self.notify()

    async def _run_loop(self):
        while self._running:
            await self.wakeup.wait()
            self.wakeup.clear()
            try:
                await self._schedule_once()
            except Exception as e:
                logger.exception("调度器循环发生异常：%s", e)
                await asyncio.sleep(1)

    async def _schedule_once(self):
        # 1. 统计每个 Bot 当前负载
        loads = {}
        for name in self.workers:
            async with get_db() as db:
                async with db.execute(
                    """SELECT COUNT(*) FROM upload_tasks
                       WHERE assigned_bot=? AND status IN ('assigned', 'uploading')""",
                    (name,),
                ) as cursor:
                    row = await cursor.fetchone()
                loads[name] = row[0]

        # 2. 找出还有空位的 Bot
        available = [name for name, load in loads.items() if load < self.max_per_bot]
        logger.info("调度检查完成：各 Worker 负载=%s，可用 Worker=%s", loads, available)
        if not available:
            logger.info("当前没有可用上传槽位，等待下一次调度")
            return

        for bot_name in available:
            free_slots = self.max_per_bot - loads[bot_name]

            # 3. 取任务（小文件优先，可改成 DESC 或 created_at）
            async with get_db() as db:
                async with db.execute(
                    """SELECT * FROM upload_tasks
                       WHERE status IN ('pending', 'retrying')
                       ORDER BY file_size ASC
                       LIMIT ?""",
                    (free_slots,),
                ) as cursor:
                    tasks = await cursor.fetchall()

            for task in tasks:
                # 4. 原子抢占
                async with get_db() as db:
                    cursor = await db.execute("""
                        UPDATE upload_tasks
                        SET status = 'assigned',
                            assigned_bot = ?,
                            assigned_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND status IN ('pending', 'retrying')
                    """, (bot_name, task['id']))
                    affected = cursor.rowcount
                    await db.commit()

                if affected > 0:
                    logger.info("任务已分配：任务ID=%s，Worker=%s", task["id"], bot_name)
                    await self.workers[bot_name].put_task(dict(task))

    async def _timeout_checker(self):
        """每分钟检查一次，回收超时任务"""
        while self._running:
            await asyncio.sleep(60)
            async with get_db() as db:
                cursor = await db.execute("""
                UPDATE upload_tasks
                SET status = 'pending',
                    assigned_bot = NULL,
                    assigned_at = NULL,
                    started_at = NULL,
                    error_msg = 'timeout recovered'
                WHERE status = 'uploading'
                  AND started_at < datetime('now', '-2 hours')
            """)
                await db.commit()
                if cursor.rowcount > 0:
                    logger.warning("已回收超时上传任务：数量=%s", cursor.rowcount)
            self.notify()