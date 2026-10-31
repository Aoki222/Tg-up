"""Worker 的列出、停用、启用和删除。真正的调度在 pipeline，这里只调注入进来的函数。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi import Path as FastPath

from ..app_auth import require_token


def register(app: FastAPI) -> None:
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

    async def _account_op(name: str, attr: str) -> dict:
        op = getattr(app.state, attr, None)
        if op is None:
            raise HTTPException(status_code=503, detail="个人号控制未就绪")
        try:
            await op(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"找不到账号: {name}") from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True, "name": name}

    @app.post("/api/accounts/{name}/open", dependencies=[Depends(require_token)])
    async def open_user(name: str = FastPath(..., pattern=r"^[a-zA-Z0-9_.-]+$")) -> dict:
        return await _account_op(name, "open_user")

    @app.post("/api/accounts/{name}/close", dependencies=[Depends(require_token)])
    async def close_user(name: str = FastPath(..., pattern=r"^[a-zA-Z0-9_.-]+$")) -> dict:
        return await _account_op(name, "close_user")

    @app.post("/api/accounts/{name}/upload", dependencies=[Depends(require_token)])
    async def join_upload(name: str = FastPath(..., pattern=r"^[a-zA-Z0-9_.-]+$")) -> dict:
        return await _account_op(name, "join_upload")

    @app.delete("/api/accounts/{name}/upload", dependencies=[Depends(require_token)])
    async def leave_upload(name: str = FastPath(..., pattern=r"^[a-zA-Z0-9_.-]+$")) -> dict:
        return await _account_op(name, "leave_upload")
