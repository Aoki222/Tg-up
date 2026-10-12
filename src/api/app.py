"""控制台 HTTP 的组装入口。

具体路径在 api/routes。SQL 在 adapters/db 和 TaskRepository。
Telegram 调用在 adapters/telegram。这个文件不写查询，也不直接连 Telegram。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..adapters.progress import ProgressHub
from ..adapters.session_login import SessionLoginService
from .. import config
from ..domain.settings_hub import SettingsHub
from ..domain.telegram_credentials import load_telegram_credentials
from ..logger import get_logger
from .app_auth import api_token_ok, require_token
from .stream_ticket import StreamTickets
from .models import ChatIdBody, FailedIdsBody, IdentityPayload, OversizedIdsBody, SliceBody
from .present import _board_item, _part_number, _resolve_dest_name, _sse
from .routes import (
    register_account,
    register_chats,
    register_settings,
    register_system,
    register_tasks,
    register_workers,
)
from .version import (
    _child_manifest_digest,
    _fetch_remote_release_sync,
    _label_revision,
    _version_cache,
)

# 测试仍从 src.api.app 引入这些名字。
__all__ = [
    "ChatIdBody",
    "FailedIdsBody",
    "IdentityPayload",
    "OversizedIdsBody",
    "SliceBody",
    "_board_item",
    "_child_manifest_digest",
    "_fetch_remote_release_sync",
    "_label_revision",
    "_part_number",
    "_resolve_dest_name",
    "_sse",
    "_version_cache",
    "api_token_ok",
    "create_api",
    "require_token",
]

DIST_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
logger = get_logger(__name__)


def create_api(
    progress_hub: ProgressHub,
    workers_provider: Callable[[], list[dict]] | None = None,
    settings_hub: SettingsHub | None = None,
    disable_worker=None,
    enable_worker=None,
    delete_worker=None,
    task_repository=None,
    reschedule=None,
    dispatch_oversized=None,
    slice_service=None,
    restart_process=None,
    chats_provider=None,
    chats_sync=None,
    chat_resolver=None,
    topic_admin=None,
    ingestor=None,
) -> FastAPI:
    """workers_provider / settings_hub 由 Application 注入，避免 API 层 import Worker。"""
    app = FastAPI(title="Tg-up", version="0.1.0")
    app.state.progress_hub = progress_hub
    app.state.workers_provider = workers_provider or (lambda: [])
    app.state.settings_hub = settings_hub
    app.state.disable_worker = disable_worker
    app.state.enable_worker = enable_worker
    app.state.delete_worker = delete_worker
    app.state.task_repository = task_repository
    app.state.reschedule = reschedule
    app.state.dispatch_oversized = dispatch_oversized
    app.state.slice_service = slice_service
    app.state.restart_process = restart_process
    app.state.chats_provider = chats_provider
    app.state.chats_sync = chats_sync
    app.state.chat_resolver = chat_resolver
    app.state.topic_admin = topic_admin
    app.state.ingestor = ingestor
    app.state.apply_credentials = None
    app.state.stream_tickets = StreamTickets()
    initial = load_telegram_credentials(config.PROJECT_DIR)
    app.state.session_login = SessionLoginService(
        config.SESSION_DIR, initial.api_id, initial.api_hash, config.TELEGRAM_PROXY
    )

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        """除探活和版本外，/api/* 在配置了 API_TOKEN 时都要令牌。

        进度流可以用短时票据。长期令牌只放在 Authorization 头里。
        """
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api/"):
            return await call_next(request)
        if path in ("/api/health", "/api/system/version"):
            return await call_next(request)
        if api_token_ok(request.headers.get("authorization")):
            return await call_next(request)
        tickets = getattr(request.app.state, "stream_tickets", None)
        if (
            path == "/api/progress/stream"
            and tickets is not None
            and tickets.valid(request.query_params.get("ticket"))
        ):
            return await call_next(request)
        return JSONResponse(
            {"detail": "需要 Authorization: Bearer <API_TOKEN>"},
            status_code=401,
        )

    register_system(app)
    register_workers(app)
    register_account(app)
    register_chats(app)
    register_settings(app)
    register_tasks(app)

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
    )
    async def unknown_api(rest: str) -> JSONResponse:
        """未知 /api 返回 JSON。不能落到前端的 index.html。"""
        del rest
        return JSONResponse({"detail": "未找到接口"}, status_code=404)

    if DIST_DIR.is_dir():
        assets = DIST_DIR / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index_html = DIST_DIR / "index.html"

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(index_html)

        # 前端路由都回到 index.html，由 Vue 再分发。
        @app.get("/{path:path}")
        async def spa_fallback(path: str):
            if path == "api" or path.startswith("api/"):
                return JSONResponse({"detail": "未找到接口"}, status_code=404)
            return FileResponse(index_html)

    return app
