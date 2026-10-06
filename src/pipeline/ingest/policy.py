"""入库决议。只读当前 UploadSettings，不写库、不发消息。

preview 是单一枚举（off / first_frame / grid），所以不会同时开两种封面。
watch_extensions 为空表示任意后缀都入库。封面只对常见视频后缀尝试 ffmpeg。

路由用父目录哈希：从文件所在目录往根找，第一次命中就是最长前缀。
没有命中时返回 None，不回退 upload.toml 的全局 chat_id。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...domain.caption import find_route, resolve_preview
from ...domain.upload_settings import PreviewMode, UploadSettings
from .filters import check_transient_file

_PREVIEWABLE_SUFFIXES = frozenset({".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".flv", ".ts", ".mpeg", ".mpg"})


@dataclass(frozen=True)
class IngestDecision:
    need_single: bool
    need_content: bool
    chat_id: int
    topic_enabled: bool
    allowed: bool
    matched: bool = False
    platform: str = ""
    dest_id: str = ""
    is_transient: bool = False
    transient_reason: str = ""


def match_folder_route(file_path: Path, settings: UploadSettings) -> tuple[str, str, int, bool] | None:
    """父目录哈希回溯。没有启用中的规则，或祖先都不在表里，返回 None，不回退全局群。"""
    route = find_route(file_path, settings)
    if route is None:
        return None
    topic_on = settings.topic_creation_enabled if route.topic_enabled is None else route.topic_enabled
    return route.platform, _route_dest(route), route.chat_id or _chat_id_of(route), topic_on


def _route_dest(route) -> str:
    if route.dest_id:
        return route.dest_id
    if route.platform == "telegram" and route.chat_id:
        return str(route.chat_id)
    return ""


def _chat_id_of(route) -> int:
    if route.chat_id:
        return route.chat_id
    if route.platform == "telegram" and route.dest_id.lstrip("-").isdigit():
        return int(route.dest_id)
    return 0


def inspect_path_route(target_path: Path, settings: UploadSettings) -> dict:
    """检查一个目录或文件当前的路由归属、目标标识以及是专属还是继承。"""
    active = [route for route in settings.routes if route.enabled and _route_dest(route)]
    if not active:
        return {"matched": False, "is_explicit": False}
    route_map = {route.path.resolve(): route for route in active}
    resolved = target_path.resolve()

    # 1. 自身就是一条路由规则（针对目录）
    route = route_map.get(resolved)
    if route is not None:
        topic_on = (
            settings.topic_creation_enabled if route.topic_enabled is None else route.topic_enabled
        )
        return {
            "matched": True,
            "is_explicit": True,
            "inherited_from": "",
            "platform": route.platform,
            "dest_id": _route_dest(route),
            "chat_id": route.chat_id or _chat_id_of(route),
            "topic_enabled": topic_on,
            "name": route.name,
        }

    # 2. 自底向上回溯父级目录（继承规则）
    for parent in resolved.parents:
        parent_route = route_map.get(parent)
        if parent_route is None:
            continue
        topic_on = (
            settings.topic_creation_enabled if parent_route.topic_enabled is None else parent_route.topic_enabled
        )
        return {
            "matched": True,
            "is_explicit": False,
            "inherited_from": str(parent_route.path),
            "platform": parent_route.platform,
            "dest_id": _route_dest(parent_route),
            "chat_id": parent_route.chat_id or _chat_id_of(parent_route),
            "topic_enabled": topic_on,
            "name": parent_route.name,
        }

    return {"matched": False, "is_explicit": False}


class IngestPolicy:
    """只根据当前 UploadSettings 做一次决议，不写库、不发消息。"""

    def decide(self, file_path: Path, settings: UploadSettings) -> IngestDecision:
        """用调用当下的 settings。同一文件稍后热更新了预览模式，已入库的不受影响。"""
        transient_res = check_transient_file(file_path)
        if transient_res.is_transient:
            return IngestDecision(
                need_single=False,
                need_content=False,
                chat_id=0,
                topic_enabled=False,
                allowed=False,
                matched=False,
                is_transient=True,
                transient_reason=transient_res.reason,
            )

        matched = match_folder_route(file_path, settings)
        suffix = file_path.suffix.lower()
        if matched is None:
            return IngestDecision(
                need_single=False,
                need_content=False,
                chat_id=0,
                topic_enabled=False,
                allowed=False,
                matched=False,
            )
        platform, dest_id, chat_id, topic_enabled = matched
        if settings.watch_extensions and suffix not in settings.watch_extensions:
            return IngestDecision(
                need_single=False,
                need_content=False,
                chat_id=chat_id,
                topic_enabled=topic_enabled,
                allowed=False,
                matched=True,
                platform=platform,
                dest_id=dest_id,
            )

        preview = resolve_preview(file_path, settings)
        previewable = suffix in _PREVIEWABLE_SUFFIXES and platform == "telegram"
        need_single = previewable and preview is PreviewMode.FIRST_FRAME
        need_content = previewable and preview is PreviewMode.GRID
        return IngestDecision(
            need_single=need_single,
            need_content=need_content,
            chat_id=chat_id,
            topic_enabled=topic_enabled and platform == "telegram",
            allowed=True,
            matched=True,
            platform=platform,
            dest_id=dest_id,
        )
