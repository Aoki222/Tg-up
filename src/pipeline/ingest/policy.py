"""入库决议。只读当前 UploadSettings，不写库、不发消息。

preview 是单一枚举（off / first_frame / grid），所以不会同时开两种封面。
watch_extensions 为空表示任意后缀都入库。封面只对常见视频后缀尝试 ffmpeg。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...domain.upload_settings import PreviewMode, UploadSettings

_PREVIEWABLE_SUFFIXES = frozenset({".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".flv", ".ts", ".mpeg", ".mpg"})


@dataclass(frozen=True)
class IngestDecision:
    need_single: bool
    need_content: bool
    chat_id: int
    topic_enabled: bool
    allowed: bool


def match_folder_route(file_path: Path, settings: UploadSettings) -> tuple[int, bool]:
    """父目录哈希回溯：从近到远，第一次命中即最长前缀。空表不建 dict。"""
    active = [route for route in settings.routes if route.enabled]
    if not active:
        return settings.chat_id, settings.topic_creation_enabled
    route_map = {route.path.resolve(): route for route in active}
    for parent in file_path.resolve().parents:
        route = route_map.get(parent)
        if route is None:
            continue
        topic_on = (
            settings.topic_creation_enabled if route.topic_enabled is None else route.topic_enabled
        )
        return route.chat_id, topic_on
    return settings.chat_id, settings.topic_creation_enabled


class IngestPolicy:
    """只根据当前 UploadSettings 做一次决议，不写库、不发消息。"""

    def decide(self, file_path: Path, settings: UploadSettings) -> IngestDecision:
        """用调用当下的 settings。同一文件稍后热更新了预览模式，已入库的不受影响。"""
        chat_id, topic_enabled = match_folder_route(file_path, settings)
        suffix = file_path.suffix.lower()
        if settings.watch_extensions and suffix not in settings.watch_extensions:
            return IngestDecision(
                need_single=False,
                need_content=False,
                chat_id=chat_id,
                topic_enabled=topic_enabled,
                allowed=False,
            )

        previewable = suffix in _PREVIEWABLE_SUFFIXES
        need_single = previewable and settings.preview is PreviewMode.FIRST_FRAME
        need_content = previewable and settings.preview is PreviewMode.GRID
        return IngestDecision(
            need_single=need_single,
            need_content=need_content,
            chat_id=chat_id,
            topic_enabled=topic_enabled,
            allowed=True,
        )
