"""控制台 HTTP，和流水线同进程。

提供 Worker 列表、进度 SSE、读写 upload.toml。
不接收外部投喂文件：视频只从监听目录进入。
Vue 构建产物在 frontend/dist，由本模块托管 / 和 /assets。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..adapters.progress import ProgressHub
from ..adapters.session_login import SessionLoginService
from ..config import API_HASH, API_ID, API_TOKEN, SESSION_DIR, TELEGRAM_PROXY
from ..domain.progress import UploadProgress
from ..domain.settings_hub import SettingsHub
from .sessions import SessionCodeBody, SessionPasswordBody, SessionStartBody, login_payload
from .settings import SettingsPayload

DIST_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """API_TOKEN 为空则放行（本机单用）。设置后 PUT /api/settings 必须带 Bearer。"""
    if not API_TOKEN:
        return
    expected = f"Bearer {API_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="需要 Authorization: Bearer <API_TOKEN>")


def create_api(
    progress_hub: ProgressHub,
    workers_provider: Callable[[], list[dict]] | None = None,
    settings_hub: SettingsHub | None = None,
    disable_worker=None,
    enable_worker=None,
    delete_worker=None,
) -> FastAPI:
    """workers_provider / settings_hub 由 Application 注入，避免 API 层 import Worker。"""
    app = FastAPI(title="uploader", version="0.1.0")
    app.state.progress_hub = progress_hub
    app.state.workers_provider = workers_provider or (lambda: [])
    app.state.settings_hub = settings_hub
    app.state.disable_worker = disable_worker
    app.state.enable_worker = enable_worker
    app.state.delete_worker = delete_worker
    app.state.session_login = SessionLoginService(SESSION_DIR, API_ID, API_HASH, TELEGRAM_PROXY)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/workers")
    async def workers() -> dict:
        return {"items": app.state.workers_provider()}

    @app.post("/api/workers/{name}/disable", dependencies=[Depends(require_token)])
    async def disable_worker(name: str) -> dict:
        op = app.state.disable_worker
        if op is None:
            raise HTTPException(status_code=503, detail="Worker 控制未就绪")
        try:
            await op(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"找不到 worker: {name}") from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True, "name": name, "enabled": False}

    @app.post("/api/workers/{name}/enable", dependencies=[Depends(require_token)])
    async def enable_worker(name: str) -> dict:
        op = app.state.enable_worker
        if op is None:
            raise HTTPException(status_code=503, detail="Worker 控制未就绪")
        try:
            await op(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"找不到 worker: {name}") from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True, "name": name, "enabled": True}

    @app.delete("/api/workers/{name}", dependencies=[Depends(require_token)])
    async def delete_worker(name: str) -> dict:
        op = app.state.delete_worker
        if op is None:
            raise HTTPException(status_code=503, detail="Worker 控制未就绪")
        try:
            await op(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"找不到 worker: {name}") from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True, "name": name, "deleted": True}

    @app.get("/api/sessions")
    async def list_sessions() -> dict:
        service: SessionLoginService = app.state.session_login
        hub: SettingsHub | None = app.state.settings_hub
        default_group = hub.get().chat_id if hub is not None else None
        return {
            "items": service.list_saved(),
            "default_group_id": default_group,
            "api_configured": True,
        }

    @app.post("/api/sessions/start", dependencies=[Depends(require_token)])
    async def start_session(payload: SessionStartBody) -> dict:
        service: SessionLoginService = app.state.session_login
        group_id = payload.group_id if payload.bind_group else None
        try:
            if payload.mode == "bot":
                result = await service.start_bot(payload.bot_token, group_id, payload.force)
            elif payload.mode == "user":
                result = await service.start_user(payload.phone, group_id, payload.force)
            else:
                raise HTTPException(status_code=400, detail="mode 只能是 bot 或 user")
        except FileExistsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return login_payload(result)

    @app.post("/api/sessions/code", dependencies=[Depends(require_token)])
    async def session_code(payload: SessionCodeBody) -> dict:
        service: SessionLoginService = app.state.session_login
        try:
            result = await service.submit_code(payload.login_id, payload.code)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return login_payload(result)

    @app.post("/api/sessions/password", dependencies=[Depends(require_token)])
    async def session_password(payload: SessionPasswordBody) -> dict:
        service: SessionLoginService = app.state.session_login
        try:
            result = await service.submit_password(payload.login_id, payload.password)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return login_payload(result)

    @app.get("/api/settings")
    async def get_settings() -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        return hub.public_dict()

    @app.put("/api/settings", dependencies=[Depends(require_token)])
    async def put_settings(payload: SettingsPayload) -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        try:
            return hub.save_from_payload(payload.model_dump())
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/progress")
    async def progress_snapshot() -> dict:
        hub: ProgressHub = app.state.progress_hub
        return {"items": [item.to_dict() for item in hub.snapshot()]}

    @app.get("/api/progress/stream")
    async def progress_stream(request: Request) -> StreamingResponse:
        """SSE：先推当前快照，再持续推送。"""
        hub: ProgressHub = request.app.state.progress_hub
        queue = hub.subscribe()

        async def event_source():
            try:
                for item in hub.snapshot():
                    yield _sse(item)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=20)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield _sse(item)
            finally:
                hub.unsubscribe(queue)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if DIST_DIR.is_dir():
        assets = DIST_DIR / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(DIST_DIR / "index.html")

    return app


def _sse(progress: UploadProgress) -> str:
    return f"event: progress\ndata: {json.dumps(progress.to_dict(), ensure_ascii=False)}\n\n"
