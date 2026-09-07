from telethon import TelegramClient
import asyncio
from ..config import API_ID, API_HASH, SESSION_FILES, OBSERVER_PATH, UPLOAD_CONCURRENCY
from ..logger import get_logger
from ..database.init import init_db
from .task_repository import TaskRepository
from .upload_scheduler import UploadScheduler
from .upload_worker import UploadWorker
from .folder_watcher import FolderWatcher
from .file_ingestor import FileIngestor

logger = get_logger(__name__)

class UploaderApplication:
    
    async def create_telegram_clients(self) -> dict:
        telegram_clients: dict = {}
        logger.info("开始加载客户端: 数量=%s", len(SESSION_FILES))
        for worker_name, session_path in SESSION_FILES.items():
            telegram_client = TelegramClient(session_path, API_ID, API_HASH)
            await telegram_client.connect()
            if not await telegram_client.is_user_authorized():
                logger.warning("[%s] 未授权, 跳过: %s", worker_name, session_path)
                continue
            current_user = await telegram_client.get_me()
            logger.info("[%s] 加载成功: %s", worker_name, current_user.username)
            telegram_clients[worker_name] = telegram_client
        return telegram_clients
    
    async def run(self) -> None:
        await init_db()
        clients = await self.create_telegram_clients()
        repository = TaskRepository()
        scheduler = UploadScheduler(repository, max_tasks_per_worker=UPLOAD_CONCURRENCY)
        watcher = FolderWatcher(OBSERVER_PATH, FileIngestor(repository, scheduler).handle_new_file)
        for worker_name, telegram_client in clients.items():
            scheduler.register_worker(UploadWorker(
                worker_name=worker_name,
                telegram_client=telegram_client,
                task_repository=repository,
                on_task_finished=scheduler.request_reschedule,
                max_concurrent_uploads=UPLOAD_CONCURRENCY,
            ))
        try:
            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(watcher.run_forever())
                for worker in scheduler.worker_map.values():
                    task_group.create_task(worker.serve_forever())
                task_group.create_task(scheduler.run_forever())
                 
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("收到退出信号，开始优雅停止")
        finally:  # 先断源，再断分发，最后排空执行；shield 防第二次 Ctrl+C 打断排空
            try:
                await asyncio.shield(self.shutdown_gracefully(watcher, scheduler, clients))
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.warning("再次收到退出信号，强制退出")
            logger.info("已优雅退出")

    async def shutdown_gracefully(self, watcher, scheduler, clients: dict) -> None:
        pending = sum(worker.task_queue.qsize() + len(worker.background_tasks)
                      for worker in scheduler.worker_map.values())
        logger.info("正在优雅退出，等待在途任务数=%s", pending)
        await watcher.stop()
        await scheduler.stop()
        for worker in scheduler.worker_map.values():
            await worker.stop(drain_timeout_seconds=60.0)
        for client in clients.values():
            try:
                await client.disconnect()
            except Exception:
                logger.exception("断开客户端失败")