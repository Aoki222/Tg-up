from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from ..config import SINGLE_PAGE_ENABLED, CONTENT_PAGE_ENABLED, VIDEO_EXTENSIONS, TARGET_CHAT_ID
from ..logger import get_logger

logger = get_logger(__name__)

@dataclass
class IngestDecision:
    need_single: bool   # 是否截首帧（快照 single_page）
    need_content: bool  # 是否截网格（快照 content_page）
    chat_id: int        # 发往 topic，默认 TARGET_CHAT_ID，后续按 folder 映射扩展


class IngestPolicy:
    def decide(self,file_path: Path) -> IngestDecision:
        is_video = file_path.suffix.lower() in VIDEO_EXTENSIONS
        need_single = bool(SINGLE_PAGE_ENABLED and is_video)
        need_content = bool(CONTENT_PAGE_ENABLED and is_video)
        
        if need_single and need_content:
            logger.warning("截图模式互斥：SINGLE_PAGE_ENABLED 与 CONTENT_PAGE_ENABLED 不可同时为 true")
            need_single = False
            
        return IngestDecision(
            need_single=need_single,
            need_content=need_content,
            chat_id=TARGET_CHAT_ID
        )