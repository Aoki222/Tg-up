"""临时与中间分片文件过滤器基类与数据契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FilterResult:
    """临时文件过滤决议"""

    is_transient: bool
    source_platform: str = ""
    reason: str = ""


class TransientFilter(ABC):
    """特定下载来源的过滤器抽象接口。"""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """来源唯一标识，如 'youtube', 'bilibili'"""
        pass

    @property
    @abstractmethod
    def fast_keywords(self) -> tuple[str, ...]:
        """Fast-Path 关键词。文件名包含这些子串时才会触发本 Filter 的正则深度检查。

        例如 ('.f', '.temp')。正常视频文件名若无这些子串，直接跳过正则，性能最大化。
        """
        pass

    @abstractmethod
    def check(self, file_path: Path) -> FilterResult | None:
        """深度规则校验。命中返回 FilterResult，无关返回 None。"""
        pass
