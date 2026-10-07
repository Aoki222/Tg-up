"""upload.toml 和监听目录树。目录树只读磁盘和任务状态，不改 Telegram。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from ...pipeline.ingest.policy import inspect_path_route
from ..app_auth import require_token
from ..present import _resolve_dest_name
from ..settings import SettingsPayload
from ...domain.settings_hub import SettingsHub


def register(app: FastAPI) -> None:
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
