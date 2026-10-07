"""路径上的话题选择。纯函数，不访问数据库或 Telegram。

topic_mode 写了就停在这一层。都没写时，topic_enabled 有值也停。
两者都没写才继续往上。最后用全局话题开关：开着是 auto，关着是 off。
"""

from __future__ import annotations

from pathlib import Path

from .caption import enabled_route_map
from .upload_settings import FolderRoute, UploadSettings


def route_topic_choice(route: FolderRoute) -> tuple[str, int | None] | None:
    """这条路由自己的决定。None 表示没写，继续往上。"""
    mode = route.topic_mode
    if mode == "fixed" and route.topic_id:
        return "fixed", int(route.topic_id)
    if mode == "off":
        return "off", None
    if mode == "auto":
        return "auto", None
    if mode == "fixed":
        return "off", None
    if route.topic_enabled is False:
        return "off", None
    if route.topic_enabled is True:
        return "auto", None
    return None


def resolve_topic(file_path: Path, settings: UploadSettings) -> tuple[str, int | None]:
    """返回 off、auto 或 fixed，以及 fixed 时的话题编号。"""
    route_map = enabled_route_map(settings)
    for parent in file_path.resolve().parents:
        route = route_map.get(parent)
        if route is None:
            continue
        choice = route_topic_choice(route)
        if choice is not None:
            return choice
    if settings.topic_creation_enabled:
        return "auto", None
    return "off", None


def routes_fixed_on_topic(settings: UploadSettings, chat_id: int, topic_id: int) -> list[str]:
    """仍指定这个话题的路径。删除话题前用来拒绝。"""
    found: list[str] = []
    for route in settings.routes:
        if route.topic_mode != "fixed" or route.topic_id != topic_id:
            continue
        route_chat = route.chat_id
        if route_chat == 0 and route.dest_id.lstrip("-").isdigit():
            route_chat = int(route.dest_id)
        if route_chat == chat_id:
            found.append(str(route.path))
    return found
