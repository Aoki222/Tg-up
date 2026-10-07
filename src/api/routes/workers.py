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
