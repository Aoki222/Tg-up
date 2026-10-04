"""控制台 HTTP，和流水线同进程。

提供 Worker 列表、进度 SSE、读写 upload.toml。
不接收外部投喂文件：视频只从监听目录进入。
Vue 构建产物在 frontend/dist，由本模块托管 / 和 /assets。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, BackgroundTasks, Path as FastPath
from pydantic import BaseModel, Field, field_validator
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..adapters.progress import ProgressHub
from ..adapters.session_login import SessionLoginService
from ..config import API_TOKEN, PROJECT_DIR, SESSION_DIR, TELEGRAM_PROXY, mask_api_hash
from ..domain.progress import UploadProgress
from ..domain.settings_hub import SettingsHub
from ..domain.telegram_credentials import load_telegram_credentials, merge_submitted_hash, save_telegram_credentials
from ..pipeline.ingest.policy import inspect_path_route
from .sessions import SessionCodeBody, SessionPasswordBody, SessionStartBody, login_payload
from .settings import SettingsPayload


class FailedIdsBody(BaseModel):
    ids: list[int] = Field(default_factory=list)


class ChatIdBody(BaseModel):
    chat_id: int


class IdentityPayload(BaseModel):
    api_id: int = Field(ge=1)
    api_hash: str = ""

    @field_validator("api_hash")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        return (value or "").strip()


DIST_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

_version_cache: dict[str, object] = {
    "checked_at": 0.0,
    "data": None,
}
_VERSION_CACHE_TTL = 1800.0


def _fetch_github_commit_sync() -> dict | None:
    url = "https://api.github.com/repos/Aoki222/Tg-up/commits/main"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Tg-up-App",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return None


def api_token_ok(authorization: str | None, access_token: str | None) -> bool:
    """API_TOKEN 为空则放行。否则 Bearer 头或 access_token 查询参数任一匹配即可。"""
    if not API_TOKEN:
        return True
    if authorization == f"Bearer {API_TOKEN}":
        return True
    return bool(access_token) and access_token == API_TOKEN


async def require_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """写接口依赖。读接口由下面的中间件统一拦。"""
    if api_token_ok(authorization, request.query_params.get("access_token")):
        return
    raise HTTPException(status_code=401, detail="需要 Authorization: Bearer <API_TOKEN>")


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
    restart_process=None,
    chats_provider=None,
    chats_sync=None,
    chat_resolver=None,
) -> FastAPI:
    """workers_provider / settings_hub 由 Application 注入，避免 API 层 import Worker。"""
    app = FastAPI(title="uploader", version="0.1.0")
    app.state.progress_hub = progress_hub
    app.state.workers_provider = workers_provider or (lambda: [])
    app.state.settings_hub = settings_hub
    app.state.disable_worker = disable_worker
    app.state.enable_worker = enable_worker
    app.state.delete_worker = delete_worker
    app.state.task_repository = task_repository
    app.state.reschedule = reschedule
    app.state.dispatch_oversized = dispatch_oversized
    app.state.restart_process = restart_process
    app.state.chats_provider = chats_provider
    app.state.chats_sync = chats_sync
    app.state.chat_resolver = chat_resolver
    app.state.apply_credentials = None
    initial = load_telegram_credentials(PROJECT_DIR)
    app.state.session_login = SessionLoginService(
        SESSION_DIR, initial.api_id, initial.api_hash, TELEGRAM_PROXY
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        """除 /api/health 与 /api/system/version 外，/api/* 在配置了 API_TOKEN 时都要令牌。OPTIONS 放行给 CORS。"""
        path = request.url.path
        if (
            request.method != "OPTIONS"
            and path.startswith("/api/")
            and path not in ("/api/health", "/api/system/version")
            and not api_token_ok(
                request.headers.get("authorization"),
                request.query_params.get("access_token"),
            )
        ):
            return JSONResponse(
                {"detail": "需要 Authorization: Bearer <API_TOKEN>"},
                status_code=401,
            )
        return await call_next(request)

    # 进程是否在听。不鉴权，给探活用。
    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    # 当前版本与远程更新检查（30分钟内存缓存，不鉴权）
    @app.get("/api/system/version")
    async def get_system_version() -> dict:
        current_raw = (os.getenv("APP_VERSION") or "local_dev").strip()
        is_dev = current_raw in ("local_dev", "dev", "")
        current_version = "dev" if is_dev else (current_raw[:7] if len(current_raw) >= 7 else current_raw)

        now = time.monotonic()
        cached_data = _version_cache.get("data")
        last_check = float(_version_cache.get("checked_at") or 0.0)
        if cached_data is not None and (now - last_check) < _VERSION_CACHE_TTL:
            return dict(cached_data)

        remote_json = await asyncio.to_thread(_fetch_github_commit_sync)
        if not remote_json:
            result = {
                "current_version": current_version,
                "remote_version": None,
                "has_update": False,
                "commit_message": "",
                "commit_url": "https://github.com/Aoki222/Tg-up",
            }
            if cached_data is not None:
                return dict(cached_data)
            return result

        remote_sha = str(remote_json.get("sha") or "")
        remote_short = remote_sha[:7] if len(remote_sha) >= 7 else remote_sha
        commit_info = remote_json.get("commit") or {}
        raw_msg = str(commit_info.get("message") or "")
        commit_msg = raw_msg.splitlines()[0] if raw_msg else ""
        html_url = str(remote_json.get("html_url") or f"https://github.com/Aoki222/Tg-up/commit/{remote_sha}")

        has_update = False
        if not is_dev and remote_sha:
            if not remote_sha.startswith(current_raw) and not current_raw.startswith(remote_sha):
                has_update = True

        result = {
            "current_version": current_version,
            "remote_version": remote_short,
            "has_update": has_update,
            "commit_message": commit_msg,
            "commit_url": html_url,
        }
        _version_cache["checked_at"] = now
        _version_cache["data"] = result
        return result

    # 当前已加载的 Session Worker：在线、队列、限流、是否禁用。
    @app.get("/api/workers")
    async def workers() -> dict:
        return {"items": app.state.workers_provider()}

    # 停用一个 Worker：不再接新任务，在途的会放回 pending。
    @app.post("/api/workers/{name}/disable", dependencies=[Depends(require_token)])
    async def disable_worker(name: str = FastPath(..., pattern=r"^[a-zA-Z0-9_.-]+$")) -> dict:
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

    # 重新启用一个被停用的 Worker。
    @app.post("/api/workers/{name}/enable", dependencies=[Depends(require_token)])
    async def enable_worker(name: str = FastPath(..., pattern=r"^[a-zA-Z0-9_.-]+$")) -> dict:
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

    # 删除 sessions/<name>.session，并卸掉对应 Worker。
    @app.delete("/api/workers/{name}", dependencies=[Depends(require_token)])
    async def delete_worker(name: str = FastPath(..., pattern=r"^[a-zA-Z0-9_.-]+$")) -> dict:
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

    # 磁盘上的 session 列表，区分用户号和 Bot。
    @app.get("/api/sessions")
    async def list_sessions() -> dict:
        service: SessionLoginService = app.state.session_login
        hub: SettingsHub | None = app.state.settings_hub
        default_group = hub.get().chat_id if hub is not None else None
        return {
            "items": service.list_saved(),
            "accounts": service.list_accounts(),
            "default_group_id": default_group,
            "api_configured": service.configured,
        }

    # 开始登录。mode=bot 用 Token；user 发验证码；qr 返回 tg://login 链接。
    @app.post("/api/sessions/start", dependencies=[Depends(require_token)])
    async def start_session(payload: SessionStartBody) -> dict:
        """登录入口：路由只做鉴权/分流，实际 Telegram 操作由 SessionLoginService 完成。"""
        service: SessionLoginService = app.state.session_login
        group_id = payload.group_id if payload.bind_group else None
        try:
            if payload.mode == "bot":
                result = await service.start_bot(payload.bot_token, group_id, payload.force)
            elif payload.mode == "user":
                result = await service.start_user(payload.phone, group_id, payload.force)
            elif payload.mode == "qr":
                result = await service.start_qr(group_id, payload.force)
            else:
                raise HTTPException(status_code=400, detail="mode 只能是 bot、user 或 qr")
        except FileExistsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return login_payload(result)

    # 轮询二维码登录：链接是否更新、是否要两步验证、是否已完成。
    @app.get("/api/sessions/login/{login_id}")
    async def poll_session_login(login_id: str) -> dict:
        """把服务端内存中的二维码状态转换为前端可消费的统一响应。"""
        service: SessionLoginService = app.state.session_login
        try:
            result = service.poll(login_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return login_payload(result)

    # 取消二维码/手机号登录，立即释放后端客户端和临时 session。
    @app.delete("/api/sessions/login/{login_id}", dependencies=[Depends(require_token)])
    async def cancel_session_login(login_id: str) -> dict:
        """前端关闭登录面板时调用，避免 pending 登录等到超时才清理。"""
        service: SessionLoginService = app.state.session_login
        await service.cancel(login_id)
        return {"ok": True, "login_id": login_id}

    # 提交手机号登录的验证码。
    @app.post("/api/sessions/code", dependencies=[Depends(require_token)])
    async def session_code(payload: SessionCodeBody) -> dict:
        """把前端验证码转交给创建手机号登录时保留的 TelegramClient。"""
        service: SessionLoginService = app.state.session_login
        try:
            result = await service.submit_code(payload.login_id, payload.code)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return login_payload(result)

    # 提交两步验证密码。手机号和二维码登录都会走到这里。
    @app.post("/api/sessions/password", dependencies=[Depends(require_token)])
    async def session_password(payload: SessionPasswordBody) -> dict:
        """把 2FA 密码转交给原登录客户端，并返回最终保存结果。"""
        service: SessionLoginService = app.state.session_login
        try:
            result = await service.submit_password(payload.login_id, payload.password)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return login_payload(result)

    # 当前凭据来源，以及掩码后的 API_HASH。不回完整 hash。
    @app.get("/api/identity")
    async def get_identity() -> dict:
        current = load_telegram_credentials(PROJECT_DIR)
        return {
            "api_id": current.api_id,
            "api_hash_masked": mask_api_hash(current.api_hash) if current.api_hash else "",
            "configured": current.configured,
            "source": current.source,
        }

    # 写入 data/telegram.json，并热注入当前进程。不改 .env，也不要求重启。
    @app.put("/api/identity", dependencies=[Depends(require_token)])
    async def put_identity(payload: IdentityPayload) -> dict:
        current = load_telegram_credentials(PROJECT_DIR)
        try:
            api_hash = merge_submitted_hash(payload.api_hash, current.api_hash)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        saved = save_telegram_credentials(PROJECT_DIR, payload.api_id, api_hash)
        callback = app.state.apply_credentials
        hot = False
        if callback is not None:
            result = callback(saved)
            if inspect.isawaitable(result):
                await result
            hot = True
        return {
            "ok": True,
            "hot_reloaded": hot,
            "restart_required": False,
            "source": saved.source,
        }

    # 先停发现和调度，再拉起新进程。凭据保存后由前端确认才调用。
    @app.post("/api/process/restart", dependencies=[Depends(require_token)])
    async def restart_process(background_tasks: BackgroundTasks) -> dict:
        op = app.state.restart_process
        if op is None:
            raise HTTPException(status_code=503, detail="重启未就绪")
        background_tasks.add_task(op)
        return {"ok": True}

    # 个人号已加入的群和频道，供设置页点选。没有用户号时 online 为 false。
    @app.get("/api/chats")
    async def list_chats() -> dict:
        provider = app.state.chats_provider
        if provider is None:
            return {"items": [], "online": False, "reason": "会话池未就绪"}
        result = await provider()
        if isinstance(result, dict):
            return result
        return {"items": result, "online": bool(result), "reason": ""}

    # 手动强制从 Telegram 全量校准本地群组/频道缓存。
    @app.post("/api/chats/sync", dependencies=[Depends(require_token)])
    async def sync_chats() -> dict:
        """调用注入的同步函数，供前端在缓存异常或需要立即刷新时使用。"""
        provider = app.state.chats_sync
        if provider is None:
            raise HTTPException(status_code=503, detail="群组缓存服务未就绪")
        result = await provider()
        if isinstance(result, dict):
            return result
        return {"items": result, "online": bool(result), "reason": ""}

    # 用 chat_id 向 Telegram 要官方标题。选群列表改走 GET /api/chats 后，这条只作补查。
    @app.post("/api/chats/resolve")
    async def resolve_chat(payload: ChatIdBody) -> dict:
        resolver = app.state.chat_resolver
        title = ""
        if resolver is not None:
            try:
                title = await resolver(int(payload.chat_id))
            except Exception as error:
                raise HTTPException(status_code=400, detail=f"找不到该群或频道: {error}") from error
        return {"id": int(payload.chat_id), "title": title or ""}

    # 当前 upload.toml：监听、封面、并发、路由、群别名、Drive 文件夹。
    @app.get("/api/settings")
    async def get_settings() -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        return hub.public_dict()

    # 整份写回 upload.toml 并热加载。只影响之后发现的文件。
    @app.put("/api/settings", dependencies=[Depends(require_token)])
    async def put_settings(payload: SettingsPayload) -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        try:
            return hub.save_from_payload(payload.model_dump())
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    # 浏览已映射路径下的子文件夹与文件（支持按需懒加载 + 路由与任务状态计算）。
    @app.get("/api/fs/nodes")
    async def list_fs_nodes(path: str | None = None) -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        settings = hub.get()
        repo = app.state.task_repository

        # 当未传 path 时，返回配置的所有监听根目录
        if not path:
            items = []
            for root in settings.observer_paths:
                resolved = root.resolve()
                exists = resolved.is_dir()
                has_children = False
                if exists:
                    try:
                        with os.scandir(resolved) as it:
                            has_children = any(not e.name.startswith(".") for e in it)
                    except OSError:
                        pass
                route_info = inspect_path_route(resolved, settings)
                if route_info.get("matched"):
                    route_info["dest_name"] = _resolve_dest_name(
                        route_info["platform"], route_info["dest_id"], settings
                    )
                items.append({
                    "path": str(resolved),
                    "name": resolved.name or str(resolved),
                    "is_dir": True,
                    "has_children": has_children,
                    "exists": exists,
                    "is_root": True,
                    "current_route": route_info,
                })
            return {"items": items, "path": ""}

        # 校验传入的路径：必须是某个已配置 observer_path 的子孙路径
        req_path = Path(path).resolve()
        is_safe = False
        for root in settings.observer_paths:
            try:
                if req_path.is_relative_to(root.resolve()):
                    is_safe = True
                    break
            except ValueError:
                pass
        if not is_safe:
            raise HTTPException(status_code=403, detail="禁止访问未映射的目录")

        if not req_path.is_dir():
            raise HTTPException(status_code=404, detail="目录不存在")

        dir_items = []
        file_entries = []
        try:
            with os.scandir(req_path) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=True):
                            dir_items.append(entry)
                        elif entry.is_file(follow_symlinks=True):
                            file_entries.append(entry)
                    except OSError:
                        continue
        except OSError as err:
            raise HTTPException(status_code=500, detail=f"读取目录失败: {err}") from err

        nodes = []
        # 处理子目录
        for d in sorted(dir_items, key=lambda e: e.name.lower()):
            d_path = Path(d.path).resolve()
            has_children = False
            try:
                with os.scandir(d_path) as sub_it:
                    has_children = any(not e.name.startswith(".") for e in sub_it)
            except OSError:
                pass
            route_info = inspect_path_route(d_path, settings)
            if route_info.get("matched"):
                route_info["dest_name"] = _resolve_dest_name(
                    route_info["platform"], route_info["dest_id"], settings
                )
            nodes.append({
                "path": str(d_path),
                "name": d.name,
                "is_dir": True,
                "has_children": has_children,
                "is_root": False,
                "current_route": route_info,
            })

        # 批量获取当前层级文件的任务状态
        file_paths = [str(Path(f.path).resolve()) for f in file_entries]
        status_map = {}
        if repo is not None and file_paths:
            try:
                status_map = await repo.get_tasks_status_by_paths(file_paths)
            except Exception:
                status_map = {}

        # 处理文件
        for f in sorted(file_entries, key=lambda e: e.name.lower()):
            f_path = Path(f.path).resolve()
            f_str = str(f_path)
            stat = f.stat()
            ext = f_path.suffix.lstrip(".").lower()
            supported_ext = bool(not settings.watch_extensions or f".{ext}" in settings.watch_extensions)
            route_info = inspect_path_route(f_path, settings)
            if route_info.get("matched"):
                route_info["dest_name"] = _resolve_dest_name(
                    route_info["platform"], route_info["dest_id"], settings
                )
            task_info = status_map.get(f_str) or {}
            task_status = task_info.get("status", "not_ingested")

            nodes.append({
                "path": f_str,
                "name": f.name,
                "is_dir": False,
                "has_children": False,
                "size": stat.st_size,
                "ext": ext,
                "supported_ext": supported_ext,
                "current_route": route_info,
                "task_status": task_status,
                "task_id": task_info.get("task_id"),
                "task_error": task_info.get("error"),
            })

        return {"items": nodes, "path": str(req_path)}

    # 没有命中路由、因此没有进入上传队列的文件。
    @app.get("/api/unmatched")
    async def list_unmatched() -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        rows = await repo.list_unmatched()
        return {"items": rows, "count": len(rows)}

    # 看板快照：进行中的任务，外加最近失败。上传中的行叠上实时进度。
    @app.get("/api/tasks")
    async def list_tasks() -> dict:
        """看板快照：SQLite 任务行叠上 ProgressHub 的实时字节/速度。"""
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        hub: ProgressHub = app.state.progress_hub
        rows = await repo.list_board_tasks()
        counts = await repo.count_board_statuses()
        latest = {item.task_id: item for item in hub.snapshot()}
        items = [_board_item(row, latest.get(int(row["id"]))) for row in rows]
        return {"items": items, "counts": counts}

    def _wake_scheduler() -> None:
        wake = app.state.reschedule
        if wake is not None:
            wake()

    # 把库里全部 failed 重置为 pending，次数归零。缺文件的跳过。
    @app.post("/api/tasks/retry-failed", dependencies=[Depends(require_token)])
    async def retry_all_failed() -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        retried, skipped, parked = await repo.requeue_all_failed()
        if retried:
            _wake_scheduler()
        return {"ok": True, "retried": retried, "skipped": skipped, "parked": parked}

    # 重试一条失败任务：failed → pending，retry_count 归零。
    @app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(require_token)])
    async def retry_task(task_id: int) -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        result = await repo.requeue_failed(task_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="找不到任务")
        if result == "not_failed":
            raise HTTPException(status_code=409, detail="只能重试失败任务")
        if result == "missing_file":
            raise HTTPException(status_code=409, detail="文件不存在")
        fresh = await repo.get_task_by_id(task_id)
        status = str(fresh.get("status") if fresh else "pending")
        if status == "pending":
            _wake_scheduler()
        return {"ok": True, "id": task_id, "status": status, "retry_count": 0}

    # 过大文件只交给个人号。没有可用个人号时明确拒绝，不回退到 Bot。
    @app.post("/api/tasks/{task_id}/dispatch-user", dependencies=[Depends(require_token)])
    async def dispatch_oversized(task_id: int) -> dict:
        dispatch = app.state.dispatch_oversized
        if dispatch is None:
            raise HTTPException(status_code=503, detail="任务分发未就绪")
        result = await dispatch(task_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="找不到任务")
        if result == "not_oversized":
            raise HTTPException(status_code=409, detail="只能把过大文件交给个人号")
        if result == "missing_file":
            raise HTTPException(status_code=409, detail="文件不存在")
        if result == "no_user":
            raise HTTPException(status_code=409, detail="没有可用的个人账号")
        if result != "ok":
            raise HTTPException(status_code=503, detail="任务分发未就绪")
        return {"ok": True, "id": task_id, "status": "assigned"}

    # 删除全部失败记录。不删磁盘上的文件。
    @app.delete("/api/tasks/failed", dependencies=[Depends(require_token)])
    async def delete_all_failed() -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        deleted = await repo.delete_all_failed()
        return {"ok": True, "deleted": deleted}

    # 按 id 删除选中的失败记录。不是 failed 的行会跳过。
    @app.post("/api/tasks/failed/delete", dependencies=[Depends(require_token)])
    async def delete_selected_failed(payload: FailedIdsBody) -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        deleted = await repo.delete_failed_ids(payload.ids)
        return {"ok": True, "deleted": deleted}

    # 删除一条失败记录。进行中的任务不能删。
    @app.delete("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
    async def delete_task(task_id: int) -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        result = await repo.delete_failed(task_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="找不到任务")
        if result == "not_failed":
            raise HTTPException(status_code=409, detail="只能清除失败或过大的任务")
        return {"ok": True, "id": task_id, "deleted": True}

    # 当前仍在 uploading 的进度快照，含服务端算好的速度。
    @app.get("/api/progress")
    async def progress_snapshot() -> dict:
        hub: ProgressHub = app.state.progress_hub
        return {"items": [item.to_dict() for item in hub.snapshot()]}

    # SSE 进度流。EventSource 不能带头，令牌放查询参数 access_token。
    @app.get("/api/progress/stream")
    async def progress_stream(request: Request) -> StreamingResponse:
        """先推当前快照，再持续推送。空闲时发 keepalive，避免代理掐连接。"""
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

        index_html = DIST_DIR / "index.html"

        # 控制台首页。
        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(index_html)

        # 前端路由（/settings/routes 等）都回到 index.html，由 Vue 再分发。
        @app.get("/{path:path}")
        async def spa_fallback(path: str) -> FileResponse:
            return FileResponse(index_html)

    return app


def _sse(progress: UploadProgress) -> str:
    return f"event: progress\ndata: {json.dumps(progress.to_dict(), ensure_ascii=False)}\n\n"


def _board_item(row: dict, progress: UploadProgress | None) -> dict:
    status = str(row.get("status") or "pending")
    if status == "retrying":
        status = "pending"
    item = {
        "id": int(row["id"]),
        "file_name": row.get("file_name") or "",
        "file_size": int(row.get("file_size") or 0),
        "folder_name": row.get("folder_name"),
        "status": status,
        "assigned_worker": row.get("assigned_worker") or row.get("assigned_bot"),
        "platform": row.get("platform") or "telegram",
        "dest_id": row.get("dest_id") or (str(row["chat_id"]) if row.get("chat_id") is not None else ""),
        "retry_count": int(row.get("retry_count") or 0),
        "max_retries": int(row.get("max_retries") or 3),
        "error": row.get("error_msg"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "percent": 0.0,
        "current": 0.0,
        "total": float(row.get("file_size") or 0),
        "speed_bps": 0.0,
        "eta_seconds": -1.0,
        "stage": None,
        "message": "",
    }
    if progress is not None:
        item["percent"] = progress.percent
        item["current"] = progress.current
        item["total"] = progress.total
        item["speed_bps"] = progress.speed_bps
        item["eta_seconds"] = progress.eta_seconds
        item["stage"] = progress.stage
        item["message"] = progress.message
        if progress.worker_name and not item["assigned_worker"]:
            item["assigned_worker"] = progress.worker_name
    return item


def _resolve_dest_name(platform: str, dest_id: str, settings) -> str:
    if platform == "telegram":
        try:
            cid = int(dest_id)
            for c in settings.chats:
                if c.chat_id == cid:
                    return c.alias.strip() or c.title.strip() or str(cid)
        except (ValueError, TypeError):
            pass
        return dest_id
    elif platform == "gdrive":
        for f in settings.drive_folders:
            if f.folder_id == dest_id:
                return f.name.strip() or dest_id
        return dest_id
    return dest_id
