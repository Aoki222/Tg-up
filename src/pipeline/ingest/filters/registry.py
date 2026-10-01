"""临时文件过滤器注册中心（提供 O(1) 预分流与按需匹配）。"""
from __future__ import annotations

from pathlib import Path

from .base import FilterResult, TransientFilter
from .generic import GENERIC_SUFFIX_MAP
from .youtube import YouTubeFilter


class FilterRegistry:
    def __init__(self) -> None:
        # 1. 静态后缀哈希表 (O(1))
        self._suffix_map = dict(GENERIC_SUFFIX_MAP)

        # 2. 动态来源平台 Filter 列表（未来有 B站、TikTok 等可直接添加）
        self._platform_filters: list[TransientFilter] = [
            YouTubeFilter(),
        ]

    def register_filter(self, flt: TransientFilter) -> None:
        """方便后续扩展动态注册新来源平台。"""
        self._platform_filters.append(flt)

    def check(self, file_path: Path) -> FilterResult:
        name = file_path.name
        suffix = file_path.suffix.lower()

        # Step 1: 隐藏文件与隐藏目录中的临时文件（如 .metube/... 或 ._xxx，排除当前路径符 '.' 和 '..'）
        if any(part.startswith(".") and part not in {".", ".."} for part in file_path.parts):
            return FilterResult(
                is_transient=True,
                source_platform="generic",
                reason="隐藏目录或系统临时文件",
            )

        # Step 2: O(1) 秒级拦截已知临时后缀（无需遍历、无需正则）
        if suffix in self._suffix_map:
            source, reason = self._suffix_map[suffix]
            return FilterResult(
                is_transient=True,
                source_platform=source,
                reason=reason,
            )

        # Step 3: Fast-Path 预筛选（针对 .mp4, .webm 等文件）
        # 只有文件名中包含某个 Filter 声明的 fast_keywords，才会进入正则深检
        for flt in self._platform_filters:
            if any(kw in name for kw in flt.fast_keywords):
                res = flt.check(file_path)
                if res is not None and res.is_transient:
                    return res

        # 完全正常的文件，直接放行
        return FilterResult(is_transient=False)


# 全局单例
_default_registry = FilterRegistry()


def check_transient_file(file_path: Path) -> FilterResult:
    """对外的统一入口，返回包含拦截原因的详细结果。"""
    return _default_registry.check(file_path)


def is_transient_file(file_path: Path) -> bool:
    """快捷布尔判断。"""
    return _default_registry.check(file_path).is_transient
