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
    allowed: bool


class IngestPolicy:
    """只根据当前 UploadSettings 做一次决议，不写库、不发消息。"""

    def decide(self, file_path: Path, settings: UploadSettings) -> IngestDecision:
        """用调用当下的 settings。同一文件稍后热更新了预览模式，已入库的不受影响。"""
        suffix = file_path.suffix.lower()
        if settings.watch_extensions and suffix not in settings.watch_extensions:
            return IngestDecision(
                need_single=False,
                need_content=False,
                chat_id=settings.chat_id,
                allowed=False,
            )

        previewable = suffix in _PREVIEWABLE_SUFFIXES
        need_single = previewable and settings.preview is PreviewMode.FIRST_FRAME
        need_content = previewable and settings.preview is PreviewMode.GRID
        return IngestDecision(
            need_single=need_single,
            need_content=need_content,
            chat_id=settings.chat_id,
            allowed=True,
        )
