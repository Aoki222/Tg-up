"""进程总装配：把发现、入库、调度、上传、session 池接在一起。

本文件不包含「怎么发 Telegram」的业务，只负责：
1. 启动顺序（建库 → 对账 → 开监听 → 加载 session）
2. 常驻循环（热更新 upload.toml、扫描 sessions/）
3. 退出顺序（先停发现，再停调度，最后排空上传）

SQLite 是任务真相源。内存队列在进程被杀后会丢，所以启动必须对账。
"""

import asyncio
from pathlib import Path

from ..adapters.after_upload import ConfigurableAfterUpload
from ..adapters.sessions import SessionPool
from ..adapters.task_store import TaskRepository
from ..adapters.telegram_transport import TelegramTransport
from ..config import API_HASH, API_ID, PROJECT_DIR, SESSION_DIR
from ..database.init import init_db
from ..domain.settings_hub import SettingsHub, ensure_upload_config
from ..logger import get_logger
from ..utils.topic_creactor import TopicCreator
from .discover import FolderWatcher, iter_existing_files
from .ingest import FileIngestor
from .schedule import UploadScheduler
from .worker import UploadWorker

logger = get_logger(__name__)


class UploaderApplication:
    """装配各阶段并管生命周期，本身不含上传业务。"""

    def __init__(self) -> None:
        # 同一时刻只允许一轮 session 增删，避免重复 connect 或卸到一半又被调度
        self._session_lock = asyncio.Lock()
        # TaskGroup 进入 async with 之后才能给后加入的 session 挂 serve_forever
        self._task_group: asyncio.TaskGroup | None = None

    async def run(self) -> None:
        """启动整条流水线，直到收到退出信号。"""
        await init_db()
        settings_hub = SettingsHub(ensure_upload_config(PROJECT_DIR), PROJECT_DIR)
        settings = settings_hub.get()
        repository = TaskRepository()
        # 内存队列是空的：把上次挂掉的 assigned/uploading/preparing 打回 pending
        recovered = await repository.reconcile_stale_tasks()
        if recovered:
            logger.warning("启动时回收未完成任务: %s", recovered)

        session_pool = SessionPool(SESSION_DIR, API_ID, API_HASH)
        scheduler = UploadScheduler(repository, settings_hub)
        after_upload = ConfigurableAfterUpload(settings_hub)
        # 话题创建用“当前任意可用 client”，session 热插拔后仍能取到人
        topic_creator = TopicCreator(session_pool.any_client, repository)
        file_queue: asyncio.Queue[Path] = asyncio.Queue()
        ingestor = FileIngestor(repository, scheduler, settings_hub, topic_creator)
        watcher = FolderWatcher(settings.observer_path, file_queue.put)

        # watchdog 只看之后的事件，启动时目录里已有的文件要补扫一遍
        for existing in iter_existing_files(settings.observer_path, settings.video_extensions):
            await file_queue.put(existing)
        if file_queue.qsize():
            logger.info("启动扫描已入队文件数=%s", file_queue.qsize())

        try:
            async with asyncio.TaskGroup() as task_group:
                self._task_group = task_group
                task_group.create_task(watcher.run_forever())
                task_group.create_task(ingestor.consume(file_queue))
                task_group.create_task(scheduler.run_forever())
                await self._sync_sessions(session_pool, scheduler, repository, after_upload, settings_hub)
                if not scheduler.worker_map:
                    logger.warning("sessions/ 下暂无可用 session，放入 *.session 后会自动加载")
                task_group.create_task(
                    self._runtime_loop(settings_hub, session_pool, scheduler, repository, after_upload)
                )
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("收到退出信号，开始优雅停止")
        finally:
            self._task_group = None
            try:
                # shield：第二次 Ctrl+C 才允许打断排空
                await asyncio.shield(
                    self.shutdown_gracefully(watcher, scheduler, session_pool)
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.warning("再次收到退出信号，强制退出")
            logger.info("已优雅退出")

    async def _runtime_loop(
        self,
        settings_hub: SettingsHub,
        session_pool: SessionPool,
        scheduler: UploadScheduler,
        repository: TaskRepository,
        after_upload: ConfigurableAfterUpload,
    ) -> None:
        """约每 2 秒：重载 upload.toml，并按磁盘上的 session 文件对齐 worker。"""
        try:
            while True:
                await asyncio.sleep(2)
                settings_hub.reload_if_changed()
                await self._sync_sessions(session_pool, scheduler, repository, after_upload, settings_hub)
        except asyncio.CancelledError:
            pass

    async def _sync_sessions(
        self,
        session_pool: SessionPool,
        scheduler: UploadScheduler,
        repository: TaskRepository,
        after_upload: ConfigurableAfterUpload,
        settings_hub: SettingsHub,
    ) -> None:
        """磁盘有的 session 就加载 worker，磁盘没了就卸载。

        未授权或连接失败的文件会跳过，下一轮再试，不把进程打挂。
        启动时目录为空是允许的，放入 *.session 后本函数会把它接上。
        """
        async with self._session_lock:
            discovered = session_pool.list_files()  # 每次重新读盘，不缓存启动时的列表
            for name, session_path in discovered.items():
                if name in scheduler.worker_map:
                    continue
                client = await session_pool.ensure_client(name, session_path)
                if client is None:
                    continue
                worker = UploadWorker(
                    worker_name=name,
                    task_repository=repository,
                    transport=TelegramTransport(client),
                    after_upload=after_upload,
                    settings_hub=settings_hub,
                    on_task_finished=scheduler.request_reschedule,
                )
                scheduler.register_worker(worker)
                if self._task_group is not None:
                    self._task_group.create_task(worker.serve_forever())
                logger.info("已加载 worker: %s", name)
                scheduler.request_reschedule()

            # 文件没了：先从调度摘掉，避免再领任务，再排空、断开
            for name in list(scheduler.worker_map):
                if name in discovered:
                    continue
                worker = scheduler.worker_map[name]
                scheduler.unregister_worker(name)
                await worker.stop(drain_timeout_seconds=30.0)
                await session_pool.remove_client(name)
                logger.info("已卸载 worker: %s", name)
                scheduler.request_reschedule()

    async def shutdown_gracefully(self, watcher, scheduler, session_pool: SessionPool) -> None:
        """优雅退出：不再收新文件 → 不再分发 → 尽量把在途上传做完 → 断开 Telegram。"""
        pending = sum(
            worker.task_queue.qsize() + len(worker.background_tasks)
            for worker in scheduler.worker_map.values()
        )
        logger.info("正在优雅退出，等待在途任务数=%s", pending)
        # 先断发现，再断分发，最后排空上传
        await watcher.stop()
        await scheduler.stop()
        for worker in list(scheduler.worker_map.values()):
            await worker.stop(drain_timeout_seconds=60.0)
        await session_pool.disconnect_all()
