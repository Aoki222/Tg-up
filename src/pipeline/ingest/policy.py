"""入库决议。只读当前 UploadSettings，不写库、不发消息。

preview 是单一枚举（off / first_frame / grid），所以不会同时开两种封面。
非视频扩展名 allowed=False，发现层仍可能入队，由本决议丢掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...domain.upload_settings import PreviewMode, UploadSettings


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
        if suffix not in settings.video_extensions:
            return IngestDecision(
                need_single=False,
                need_content=False,
                chat_id=settings.chat_id,
                allowed=False,
            )

        need_single = settings.preview is PreviewMode.FIRST_FRAME
        need_content = settings.preview is PreviewMode.GRID
        return IngestDecision(
            need_single=need_single,
            need_content=need_content,
            chat_id=settings.chat_id,
            allowed=True,
        )
