"""upload.toml 和监听目录树。目录树只读磁盘和任务状态，不改 Telegram。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from pydantic import BaseModel

from ...pipeline.ingest.policy import inspect_path_route, path_is_inside_roots, watch_roots
from ..app_auth import require_token
from ..fs_browser import fs_browser
from ..present import _resolve_dest_name
from ..responses import SettingsPublicModel, publish
from ..settings import SettingsPayload
from ...domain.settings_hub import SettingsConflict, SettingsHub


class TogglePathPayload(BaseModel):
    path: str


def register(app: FastAPI) -> None:
    # 当前 upload.toml：监听、封面、并发、路由、群别名、Drive 文件夹。
    @app.get("/api/settings")
    async def get_settings() -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        return publish(SettingsPublicModel, hub.public_dict())

    # 整份写回 upload.toml 并热加载。只影响之后发现的文件。
    @app.put("/api/settings", dependencies=[Depends(require_token)])
    async def put_settings(payload: SettingsPayload) -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        data = payload.model_dump()
        revision = data.pop("revision", None)
        try:
            return hub.save_from_payload(data, expected_revision=revision)
        except SettingsConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    # 快捷切换 L1：全局自动监控总开关
    @app.post("/api/settings/watch/toggle", dependencies=[Depends(require_token)])
    async def toggle_global_watch() -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        def apply(current: dict) -> None:
            new_state = not current.get("watch_enabled", True)
            current["watch_enabled"] = new_state
            for root in current.get("observer_roots", []):
                root["enabled"] = new_state

        saved = hub.mutate(apply)
        new_state = saved.get("watch_enabled", True)
        return {
            "watch_enabled": new_state,
            "observer_roots": saved.get("observer_roots", []),
        }

    # 快捷切换 L2：单个物理根路径启停
    @app.post("/api/settings/roots/toggle", dependencies=[Depends(require_token)])
    async def toggle_root_path(payload: TogglePathPayload) -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        target_path = payload.path.strip()
        target_resolved = Path(target_path).resolve()
        found = {"ok": False}

        def apply(current: dict) -> None:
            roots = current.get("observer_roots", [])
            for root in roots:
                if Path(root["path"]).resolve() == target_resolved:
                    root["enabled"] = not root.get("enabled", True)
                    found["ok"] = True
                    break
            if not found["ok"]:
                return False
            active_count = sum(1 for root in roots if root.get("enabled", True))
            current["watch_enabled"] = active_count > 0
            return None

        saved = hub.mutate(apply)
        if saved is None:
            raise HTTPException(status_code=404, detail="未找到该物理根路径配置")
        return {
            "path": target_path,
            "observer_roots": saved.get("observer_roots", []),
            "watch_enabled": saved.get("watch_enabled", True),
        }

    # 快捷切换 L3：单个子目录路由启停
    @app.post("/api/settings/routes/toggle", dependencies=[Depends(require_token)])
    async def toggle_route_path(payload: TogglePathPayload) -> dict:
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        target_path = payload.path.strip()
        target_resolved = Path(target_path).resolve()

        def apply(current: dict) -> None:
            routes = list(current.get("routes", []))
            existing_idx = None
            for idx, route in enumerate(routes):
                if Path(route["path"]).resolve() == target_resolved:
                    existing_idx = idx
                    break
            if existing_idx is not None:
                target_route = routes[existing_idx]
                current_enabled = target_route.get("enabled", True)
                if not current_enabled:
                    if target_route.get("auto_blocked") or target_route.get("dest_id") == "blocked":
                        routes.pop(existing_idx)
                    else:
                        target_route["enabled"] = True
                else:
                    target_route["enabled"] = False
            else:
                settings = hub.get()
                inherited = inspect_path_route(target_resolved, settings)
                platform = inherited.get("platform") or "telegram"
                dest_id = inherited.get("dest_id") or ""
                chat_id = inherited.get("chat_id") or 0
                if not dest_id and settings.chat_id:
                    platform = "telegram"
                    dest_id = str(settings.chat_id)
                    chat_id = settings.chat_id
                if not dest_id:
                    dest_id = "blocked"
                    chat_id = 0
                routes.append({
                    "name": target_resolved.name or str(target_resolved),
                    "path": str(target_resolved),
                    "enabled": False,
                    "auto_blocked": True,
                    "platform": platform,
                    "dest_id": dest_id,
                    "chat_id": chat_id,
                })
            current["routes"] = routes

        saved = hub.mutate(apply) or {}
        return {"path": target_path, "routes": saved.get("routes", [])}

    class RouteEdit(BaseModel):
        path: str
        action: str = "delete"

    @app.patch("/api/settings/routes", dependencies=[Depends(require_token)])
    async def patch_route(body: RouteEdit) -> dict:
        """只改一条路由。删除临时阻断时用，避免整份配置互相覆盖。"""
        hub: SettingsHub | None = app.state.settings_hub
        if hub is None:
            raise HTTPException(status_code=503, detail="配置服务未就绪")
        if body.action != "delete":
            raise HTTPException(status_code=400, detail="action 目前只支持 delete")
        target = Path(body.path.strip()).resolve()

        def apply(current: dict) -> None:
            current["routes"] = [
                route
                for route in current.get("routes", [])
                if Path(str(route.get("path") or "")).resolve() != target
            ]

        saved = hub.mutate(apply) or {}
        return {"path": str(target), "routes": saved.get("routes", []), "revision": saved.get("revision")}

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
            raw_roots = await fs_browser.list_roots(settings.observer_paths)
            items = []
            for node in raw_roots:
                route_info = inspect_path_route(Path(node.path), settings)
                if route_info.get("matched"):
                    route_info["dest_name"] = _resolve_dest_name(
                        route_info["platform"], route_info["dest_id"], settings
                    )
                items.append({
                    "path": node.path,
                    "name": node.name,
                    "is_dir": True,
                    "has_children": node.has_children,
                    "exists": node.exists,
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

        try:
            raw_nodes = await fs_browser.list_dir(req_path, watch_roots(settings))
        except OSError as err:
            raise HTTPException(status_code=500, detail=str(err)) from err

        file_paths = [node.path for node in raw_nodes if not node.is_dir]
        status_map = {}
        if repo is not None and file_paths:
            try:
                status_map = await repo.get_tasks_status_by_paths(file_paths)
            except Exception:
                status_map = {}

        nodes = []
        for node in raw_nodes:
            node_path = Path(node.path)
            route_info = inspect_path_route(node_path, settings)
            if route_info.get("matched"):
                route_info["dest_name"] = _resolve_dest_name(
                    route_info["platform"], route_info["dest_id"], settings
                )

            if node.is_dir:
                nodes.append({
                    "path": node.path,
                    "name": node.name,
                    "is_dir": True,
                    "has_children": node.has_children,
                    "is_root": False,
                    "current_route": route_info,
                })
            else:
                task_info = status_map.get(node.path) or {}
                supported_ext = bool(
                    not settings.watch_extensions or f".{node.ext}" in settings.watch_extensions
                )
                nodes.append({
                    "path": node.path,
                    "name": node.name,
                    "is_dir": False,
                    "has_children": False,
                    "size": node.size,
                    "ext": node.ext,
                    "supported_ext": supported_ext,
                    "current_route": route_info,
                    "task_status": task_info.get("status", "not_ingested"),
                    "task_id": task_info.get("task_id"),
                    "task_error": task_info.get("error"),
                })

        return {"items": nodes, "path": str(req_path)}
