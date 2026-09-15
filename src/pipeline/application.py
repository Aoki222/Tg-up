"""进程总装配：把发现、入库、调度、上传、session 池、控制台 API 接在一起。

本文件不包含「怎么发 Telegram」的业务，只负责生命周期：

1. 启动：建库 → 对账幽灵任务 → 开监听/扫盘 → 加载 session → 起 FastAPI
2. 运行：约 2 秒热更新 upload.toml，并按 sessions/ 增删 Worker
3. 退出：先停发现，再停调度，排空在途上传，最后断开 Telegram

文件只从监听目录进入（watchdog + 启动扫盘），没有 HTTP 投喂。
SQLite 是任务真相源。内存队列在进程被杀后会丢，所以启动必须对账。
TaskGroup 里任一子任务非取消异常会带崩整组，各循环内部应自行吞掉业务异常。
"""

import asyncio
import os
import signal
import threading
import time
from pathlib import Path

from ..adapters.after_upload import ConfigurableAfterUpload
from ..adapters.disabled_workers import DisabledWorkers
from ..adapters.progress import FanoutReporter, LogProgressBar, ProgressHub
from ..adapters.session_login import unlink_session
from ..adapters.sessions import SessionPool
from ..adapters.task_store import TaskRepository
from ..adapters.telegram_transport import TelegramTransport
from ..api.app import create_api
from ..api.workers import snapshot_workers
from ..config import API_HASH, API_HOST, API_ID, API_PORT, PROJECT_DIR, SESSION_DIR
from ..database.init import init_db
from ..domain.settings_hub import SettingsHub, ensure_upload_config
from ..logger import get_logger
from ..utils.topic_creactor import TopicCreator
from .discover import FolderWatcher, iter_existing_files_many
from .ingest import FileIngestor, PreviewJob, PreviewPool
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
        self.progress_hub = ProgressHub()
        self.progress_reporter = FanoutReporter(self.progress_hub, LogProgressBar())
        self._scheduler = None
        self._session_pool = None
        self._settings_hub = None
        self._repository = None
        self._after_upload = None
        self._disabled = DisabledWorkers(PROJECT_DIR / "data" / "disabled_workers.json")
        self._stop = asyncio.Event()
        self._watcher = None
        self._file_queue = None
        self._ingestor = None
        self._preview_pool = None
        self._uvicorn = None
        self._shutting_down = False
        self._finished = False
        self._bg_tasks: list[asyncio.Task] = []

    async def run(self) -> None:
        """启动整条流水线，直到收到 SIGINT/SIGTERM。

        第一次信号：按顺序停发现 → 入库 → 调度 → 排空上传 → 关 HTTP。
        第二次信号：不再等排空，强制退出。
        不把 TaskGroup 取消当作第一退出路径，避免把在途 send_file 直接掐掉。
        """
        logger.info("正在启动")
        self._install_stop_signals()
        await init_db()
        settings_hub = SettingsHub(ensure_upload_config(PROJECT_DIR), PROJECT_DIR)
        self._settings_hub = settings_hub
        settings = settings_hub.get()
        repository = TaskRepository()
        self._repository = repository
        recovered = await repository.reconcile_stale_tasks()
        if recovered:
            logger.warning("启动时回收未完成任务: %s", recovered)

        session_pool = SessionPool(SESSION_DIR, API_ID, API_HASH)
        scheduler = UploadScheduler(repository, settings_hub)
        self._session_pool = session_pool
        self._scheduler = scheduler
        after_upload = ConfigurableAfterUpload(settings_hub)
        self._after_upload = after_upload
        topic_creator = TopicCreator(session_pool.any_client, repository)
        file_queue: asyncio.Queue[Path | None] = asyncio.Queue()
        preview_pool = PreviewPool(repository, scheduler, settings_hub)
        ingestor = FileIngestor(
            repository, scheduler, settings_hub, topic_creator, preview_pool=preview_pool
        )
        self._preview_pool = preview_pool
        watcher = FolderWatcher(file_queue.put)
        usable = [path for path in settings.observer_paths if path.is_dir()]
        watcher.apply_paths(usable)
        self._ingestor = ingestor
        self._watcher = watcher
        self._file_queue = file_queue

        for existing in iter_existing_files_many(usable, settings.watch_extensions):
            await file_queue.put(existing)
        if file_queue.qsize():
            logger.info("启动扫描已入队文件数=%s", file_queue.qsize())

        try:
            async with asyncio.TaskGroup() as task_group:
                self._task_group = task_group

                def spawn(coro, name: str) -> None:
                    self._bg_tasks.append(task_group.create_task(coro, name=name))

                spawn(watcher.run_forever(), "watcher")
                spawn(ingestor.consume(file_queue), "ingest")
                spawn(preview_pool.run_forever(), "preview")
                spawn(scheduler.run_forever(), "scheduler")
                for row in await repository.fetch_preparing_tasks():
                    preview_pool.submit(
                        PreviewJob(
                            task_id=int(row["id"]),
                            file_path=Path(row["file_path"]),
                            need_single=bool(row.get("single_page")),
                            need_content=bool(row.get("content_page")),
                        )
                    )
                spawn(
                    self._runtime_loop(settings_hub, session_pool, scheduler, repository, after_upload),
                    "runtime",
                )
                spawn(self._serve_api(), "http")
                logger.info("已启动  控制台 http://%s:%s/", API_HOST, API_PORT)
                await self._stop.wait()
                await self._shutdown_gracefully()
                for task in self._bg_tasks:
                    if not task.done():
                        task.cancel()
        except asyncio.CancelledError:
            if not self._stop.is_set():
                logger.info("任务被取消，开始停止")
                await self._shutdown_gracefully()
        except BaseExceptionGroup as group:
            if not all(isinstance(item, asyncio.CancelledError) for item in group.exceptions):
                logger.exception("子任务异常退出")
                await self._shutdown_gracefully()
                raise
        finally:
            self._finished = True
            self._task_group = None
            logger.info("已退出")

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
            while not self._stop.is_set():
                settings_hub.reload_if_changed()
                await self._sync_watch_paths()
                await self._sync_sessions(
                    session_pool, scheduler, repository, after_upload, settings_hub
                )
                if not scheduler.worker_map and not self._stop.is_set():
                    logger.warning("sessions/ 下暂无可用 session，放入 *.session 后会自动加载")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=2)
                    break
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass

    def _install_stop_signals(self) -> None:
        loop = asyncio.get_running_loop()

        def ask_stop(*_args) -> None:
            # 必须在信号处理函数里就能二次退出：循环卡住时 call_soon 根本跑不起来
            if self._stop.is_set():
                os._exit(1)
            logger.info("收到退出信号，开始停止")
            self._stop.set()
            try:
                loop.call_soon_threadsafe(lambda: None)
            except Exception:
                os._exit(1)
            threading.Thread(target=self._force_exit_if_stuck, daemon=True).start()

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, ask_stop)
        except NotImplementedError:
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    signal.signal(sig, ask_stop)
                except (ValueError, OSError):
                    pass

    def _force_exit_if_stuck(self) -> None:
        time.sleep(10)
        if not self._finished:
            logger.warning("停止超时，强制退出")
            os._exit(1)

    async def _sync_watch_paths(self) -> None:
        """配置里的监听目录和 watchdog 对齐；新加的目录补扫已有文件。"""
        if self._watcher is None or self._settings_hub is None or self._file_queue is None:
            return
        settings = self._settings_hub.get()
        usable = [path for path in settings.observer_paths if path.is_dir()]
        added = self._watcher.apply_paths(usable)
        if not added:
            return
        queued = 0
        for path in iter_existing_files_many(added, settings.watch_extensions):
            await self._file_queue.put(path)
            queued += 1
        logger.info("监听目录已更新: %s  新目录扫入 %s 个文件", [str(p) for p in added], queued)

    async def _sync_sessions(
        self,
        session_pool: SessionPool,
        scheduler: UploadScheduler,
        repository: TaskRepository,
        after_upload: ConfigurableAfterUpload,
        settings_hub: SettingsHub,
    ) -> None:
        """磁盘有且未禁用才加载；禁用或文件没了则卸载（文件是否删除由调用方决定）。"""
        async with self._session_lock:
            discovered = session_pool.list_files()
            for name, session_path in discovered.items():
                if name in self._disabled:
                    if name in scheduler.worker_map:
                        await self._unload_worker(name, reason="worker disabled", in_flight_timeout=0)
                    continue
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
                    progress_reporter=self.progress_reporter,
                    session_pool=session_pool,
                    session_path=session_path,
                )
                scheduler.register_worker(worker)
                if self._task_group is not None:
                    self._task_group.create_task(worker.serve_forever())
                logger.info("已加载 worker: %s", name)
                scheduler.request_reschedule()

            for name in list(scheduler.worker_map):
                if name in discovered and name not in self._disabled:
                    continue
                reason = "worker disabled" if name in self._disabled else "session file removed"
                await self._unload_worker(name, reason=reason, in_flight_timeout=0)
                logger.info("已卸载 worker: %s", name)

    async def _serve_api(self) -> None:
        """和流水线同进程提供 SSE，Vue 以后只对接这个 HTTP 服务。"""
        import uvicorn

        def workers_provider() -> list[dict]:
            if self._scheduler is None or self._session_pool is None:
                return []
            return snapshot_workers(self._scheduler, self._session_pool, self._disabled)

        api = create_api(
            self.progress_hub,
            workers_provider,
            self._settings_hub,
            disable_worker=self.disable_worker,
            enable_worker=self.enable_worker,
            delete_worker=self.delete_worker,
        )
        config = uvicorn.Config(
            api,
            host=API_HOST,
            port=API_PORT,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        # 信号由 Application 处理，避免 uvicorn 再抢 Ctrl+C
        server.install_signal_handlers = False
        self._uvicorn = server
        await server.serve()

    async def _shutdown_gracefully(self) -> None:
        """先停入口，再停分发，排空在途上传，最后关 HTTP 和 Telegram。"""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._stop.set()
        watcher = self._watcher
        scheduler = self._scheduler
        session_pool = self._session_pool
        ingestor = self._ingestor
        pending = 0
        if scheduler is not None:
            pending = sum(
                worker.task_queue.qsize() + len(worker.background_tasks)
                for worker in scheduler.worker_map.values()
            )
        logger.info("正在立即停止，在途任务约 %s（不等待上传结束）", pending)
        if watcher is not None:
            await watcher.stop()
        if ingestor is not None:
            await ingestor.stop()
        if self._preview_pool is not None:
            await self._preview_pool.stop()
        if scheduler is not None:
            await scheduler.stop()
            names = list(scheduler.worker_map)
            if names:
                results = await asyncio.gather(
                    *(
                        self._unload_worker(name, reason="process stopping", in_flight_timeout=0)
                        for name in names
                    ),
                    return_exceptions=True,
                )
                for name, result in zip(names, results, strict=True):
                    if isinstance(result, BaseException):
                        logger.exception("卸载 worker 失败: %s", name, exc_info=result)
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        if session_pool is not None:
            await session_pool.disconnect_all()
        logger.info("资源已释放")

    async def _unload_worker(self, name: str, *, reason: str, in_flight_timeout: float) -> None:
        scheduler = self._scheduler
        session_pool = self._session_pool
        repository = self._repository
        if scheduler is None or session_pool is None or repository is None:
            return
        worker = scheduler.worker_map.get(name)
        if worker is not None:
            scheduler.unregister_worker(name)
            await worker.abort_and_release(reason, in_flight_timeout=in_flight_timeout)
        released = await repository.release_tasks_for_worker(name, reason)
        if released:
            logger.info("[%s] 已释放挂起任务 %s 条", name, released)
        await session_pool.remove_client(name)
        scheduler.request_reschedule()

    async def disable_worker(self, name: str) -> None:
        if name.startswith("_tmp_"):
            raise ValueError("不能操作临时登录 session")
        pool = self._session_pool
        if pool is None or (name not in pool.list_files() and (self._scheduler is None or name not in self._scheduler.worker_map)):
            raise KeyError(name)
        async with self._session_lock:
            self._disabled.add(name)
            await self._unload_worker(name, reason="worker disabled", in_flight_timeout=20)
            logger.info("已禁用 worker: %s", name)

    async def enable_worker(self, name: str) -> None:
        if name.startswith("_tmp_"):
            raise ValueError("不能操作临时登录 session")
        pool = self._session_pool
        if pool is None or name not in pool.list_files():
            raise KeyError(name)
        self._disabled.discard(name)
        after_upload = self._after_upload
        repository = self._repository
        settings_hub = self._settings_hub
        scheduler = self._scheduler
        if after_upload is None or repository is None or settings_hub is None or scheduler is None:
            return
        await self._sync_sessions(pool, scheduler, repository, after_upload, settings_hub)
        logger.info("已启用 worker: %s", name)

    async def delete_worker(self, name: str) -> None:
        if name.startswith("_tmp_"):
            raise ValueError("不能删除临时登录 session")
        pool = self._session_pool
        if pool is None:
            raise KeyError(name)
        files = pool.list_files()
        if name not in files and (self._scheduler is None or name not in self._scheduler.worker_map):
            raise KeyError(name)
        async with self._session_lock:
            await self._unload_worker(name, reason="worker deleted", in_flight_timeout=20)
            self._disabled.discard(name)
            session_path = files.get(name) or (pool.session_dir / name)
            unlink_session(session_path)
            logger.info("已删除 session 文件: %s", name)
