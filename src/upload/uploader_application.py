import asyncio
from pathlib import Path

from telethon import TelegramClient

from ..adapters.after_upload import ConfigurableAfterUpload
from ..adapters.scan_discovery import iter_existing_files
from ..adapters.telegram_transport import TelegramTransport
from ..config import API_HASH, API_ID, PROJECT_DIR, SESSION_FILES
from ..database.init import init_db
from ..domain.settings_hub import SettingsHub, ensure_upload_config
from ..logger import get_logger
from ..utils.topic_creactor import TopicCreator
from .file_ingestor import FileIngestor
from .folder_watcher import FolderWatcher
from .task_repository import TaskRepository
from .upload_scheduler import UploadScheduler
from .upload_worker import UploadWorker

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
        if not telegram_clients:
            raise RuntimeError("没有可用的 Telegram session，无法启动")
        return telegram_clients

    async def run(self) -> None:
        await init_db()
        settings_hub = SettingsHub(ensure_upload_config(PROJECT_DIR), PROJECT_DIR)
        settings = settings_hub.get()
        clients = await self.create_telegram_clients()
        repository = TaskRepository()
        recovered = await repository.reconcile_stale_tasks()
        if recovered:
            logger.warning("启动时回收未完成任务: %s", recovered)

        scheduler = UploadScheduler(repository, settings_hub)
        topic_client = next(iter(clients.values()))
        topic_creator = TopicCreator(topic_client, repository)
        after_upload = ConfigurableAfterUpload(settings_hub)
        file_queue: asyncio.Queue[Path] = asyncio.Queue()
        ingestor = FileIngestor(repository, scheduler, settings_hub, topic_creator)
        watcher = FolderWatcher(settings.observer_path, file_queue.put)

        for worker_name, telegram_client in clients.items():
            scheduler.register_worker(
                UploadWorker(
                    worker_name=worker_name,
                    task_repository=repository,
                    transport=TelegramTransport(telegram_client),
                    after_upload=after_upload,
                    settings_hub=settings_hub,
                    on_task_finished=scheduler.request_reschedule,
                )
            )

        for existing in iter_existing_files(settings.observer_path, settings.video_extensions):
            await file_queue.put(existing)
        if file_queue.qsize():
            logger.info("启动扫描已入队文件数=%s", file_queue.qsize())

        try:
            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(watcher.run_forever())
                task_group.create_task(ingestor.consume(file_queue))
                task_group.create_task(self._reload_settings_loop(settings_hub))
                for worker in scheduler.worker_map.values():
                    task_group.create_task(worker.serve_forever())
                task_group.create_task(scheduler.run_forever())
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("收到退出信号，开始优雅停止")
        finally:
            try:
                await asyncio.shield(self.shutdown_gracefully(watcher, scheduler, clients))
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.warning("再次收到退出信号，强制退出")
            logger.info("已优雅退出")

    async def _reload_settings_loop(self, settings_hub: SettingsHub) -> None:
        try:
            while True:
                await asyncio.sleep(2)
                settings_hub.reload_if_changed()
        except asyncio.CancelledError:
            pass

    async def shutdown_gracefully(self, watcher, scheduler, clients: dict) -> None:
        pending = sum(
            worker.task_queue.qsize() + len(worker.background_tasks)
            for worker in scheduler.worker_map.values()
        )
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
