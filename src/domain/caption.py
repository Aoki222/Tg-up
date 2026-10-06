"""入库说明。按路由往上找模板，再用正则替换占位符，不跑模板引擎。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .upload_settings import FolderRoute, PreviewMode, UploadSettings

_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CAPTION_LIMIT = 1024
_TEMPLATE_LIMIT = 2000
_UNITS = ("B", "KB", "MB", "GB", "TB")


def clip_template(text: str) -> str:
    return text[:_TEMPLATE_LIMIT]


def enabled_route_map(settings: UploadSettings) -> dict[Path, FolderRoute]:
    """启用且有投递目标的路由，键是解析后的目录。"""
    return {
        route.path.resolve(): route
        for route in settings.routes
        if route.enabled and _route_dest(route)
    }


def find_route(file_path: Path, settings: UploadSettings) -> FolderRoute | None:
    """离文件最近的启用路由。决定群、话题，以及 {route}、{rel_path}。"""
    route_map = enabled_route_map(settings)
    for parent in file_path.resolve().parents:
        route = route_map.get(parent)
        if route is not None:
            return route
    return None


def resolve_caption_template(file_path: Path, settings: UploadSettings) -> str:
    """最近一条写了说明的路由。空字符串表示明确不要说明。都没写则用全局。"""
    route_map = enabled_route_map(settings)
    for parent in file_path.resolve().parents:
        route = route_map.get(parent)
        if route is None or route.caption_template is None:
            continue
        return route.caption_template
    return settings.caption_template


def resolve_preview(file_path: Path, settings: UploadSettings) -> PreviewMode:
    """最近一条写了封面的路由。都没写则用全局封面模式。"""
    route_map = enabled_route_map(settings)
    for parent in file_path.resolve().parents:
        route = route_map.get(parent)
        if route is None or route.preview is None:
            continue
        return route.preview
    return settings.preview


def render_caption(
    template: str,
    file_path: Path,
    *,
    size: int,
    mtime: float | None,
    route: FolderRoute,
) -> str:
    """替换已知占位符。不认识的原样留下，{{ 和 }} 写成字面花括号。结果最长 1024。"""
    if not template:
        return ""
    values = _values(file_path, size=size, mtime=mtime, route=route)
    parts: list[str] = []
    position = 0
    for match in _TOKEN.finditer(template):
        parts.append(template[position : match.start()])
        name = match.group(1)
        token = match.group(0)
        if token == "{{":
            parts.append("{")
        elif token == "}}":
            parts.append("}")
        elif name in values:
            parts.append(values[name])
        else:
            parts.append(token)
        position = match.end()
    parts.append(template[position:])
    return "".join(parts).strip()[:_CAPTION_LIMIT]


def build_ingest_caption(file_path: Path, settings: UploadSettings, file_size: int) -> str:
    """监听入库时生成说明。没有命中路由或模板为空时返回空字符串。"""
    route = find_route(file_path, settings)
    template = resolve_caption_template(file_path, settings)
    if route is None or not template:
        return ""
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        mtime = None
    return render_caption(template, file_path, size=file_size, mtime=mtime, route=route)


def _values(file_path: Path, *, size: int, mtime: float | None, route: FolderRoute) -> dict[str, str]:
    resolved = file_path.resolve()
    route_path = route.path.resolve()
    try:
        relative = resolved.relative_to(route_path).as_posix()
    except ValueError:
        relative = resolved.name
    route_name = route.name.strip() or route_path.name
    if mtime is None:
        date = ""
    else:
        date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    suffix = resolved.suffix[1:] if resolved.suffix.startswith(".") else resolved.suffix
    return {
        "file_name": resolved.name,
        "stem": resolved.stem,
        "ext": suffix.lower(),
        "folder": resolved.parent.name,
        "rel_path": relative,
        "route": route_name,
        "size": _format_size(size),
        "date": date,
    }


def _format_size(size: int) -> str:
    value = float(max(0, size))
    unit = _UNITS[-1]
    for unit in _UNITS:
        if unit == "TB" or value < 1024:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} B"
    return f"{value:.1f} {unit}"


def _route_dest(route: FolderRoute) -> str:
    if route.dest_id:
        return route.dest_id
    if route.platform == "telegram" and route.chat_id:
        return str(route.chat_id)
    return ""
