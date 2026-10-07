"""登录、凭据和进程重启。Telegram 连接在 SessionLoginService，这里只做请求分流。"""

from __future__ import annotations

import inspect

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException

from ...domain.settings_hub import SettingsHub
from ...domain.telegram_credentials import (
    load_telegram_credentials,
    merge_submitted_hash,
    save_telegram_credentials,
)
from ... import config
from ...config import mask_api_hash
from ..app_auth import require_token
from ..models import IdentityPayload
from ..sessions import (
    SessionCodeBody,
    SessionPasswordBody,
    SessionStartBody,
    login_payload,
)
from ...adapters.session_login import SessionLoginService


def register(app: FastAPI) -> None:
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
        current = load_telegram_credentials(config.PROJECT_DIR)
        return {
            "api_id": current.api_id,
            "api_hash_masked": mask_api_hash(current.api_hash) if current.api_hash else "",
            "configured": current.configured,
            "source": current.source,
        }

    # 写入 data/telegram.json，并热注入当前进程。不改 .env，也不要求重启。
    @app.put("/api/identity", dependencies=[Depends(require_token)])
    async def put_identity(payload: IdentityPayload) -> dict:
        current = load_telegram_credentials(config.PROJECT_DIR)
        try:
            api_hash = merge_submitted_hash(payload.api_hash, current.api_hash)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        saved = save_telegram_credentials(config.PROJECT_DIR, payload.api_id, api_hash)
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
