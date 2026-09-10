from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain.upload_settings import PreviewMode, UploadSettings
from ..logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class IngestDecision:
    need_single: bool
    need_content: bool
    chat_id: int
    allowed: bool


class IngestPolicy:
    def decide(self, file_path: Path, settings: UploadSettings) -> IngestDecision:
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
