"""YouTube / yt-dlp / MeTube 专属临时中间文件过滤器。

针对以下场景：
1. YouTube DASH 音视频分轨中间件:
   - 4K/1080p 纯视频轨 (无音频): 如 name.f401.mp4, name.f137.mp4
   - Opus 纯音频轨 (无画面): 如 name.f251.webm, name.f140.m4a
2. yt-dlp ffmpeg 合并过渡临时文件:
   - 如 name.temp.webm, name.temp.mp4
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import FilterResult, TransientFilter

# 匹配 YouTube DASH 格式分轨: .f<数字>.<音视频常见后缀>
_YT_DASH_SPLIT_REGEX = re.compile(
    r"\.f(?P<fid>\d+)\.(?P<ext>mp4|webm|mkv|mov|m4a|mp3|opus|ogg|flv)$",
    re.IGNORECASE,
)

# 匹配 yt-dlp 合并过程的临时文件: .temp.<音视频常见后缀>
_YT_TEMP_MERGE_REGEX = re.compile(
    r"\.temp\.(?P<ext>mp4|webm|mkv|mov|m4a|flv|webm)$",
    re.IGNORECASE,
)


class YouTubeFilter(TransientFilter):
    @property
    def source_id(self) -> str:
        return "youtube"

    @property
    def fast_keywords(self) -> tuple[str, ...]:
        # 只要文件名里没有 '.f' 且没有 '.temp'，就绝不调用正则！
        return (".f", ".temp")

    def check(self, file_path: Path) -> FilterResult | None:
        name = file_path.name

        # 1. 检查 YouTube DASH 分轨（如 .f401.mp4, .f251.webm）
        dash_match = _YT_DASH_SPLIT_REGEX.search(name)
        if dash_match:
            fid = dash_match.group("fid")
            ext = dash_match.group("ext")
            return FilterResult(
                is_transient=True,
                source_platform=self.source_id,
                reason=f"YouTube DASH 音视频分轨中间件 (.f{fid}.{ext})",
            )

        # 2. 检查合并过渡临时文件（如 .temp.webm）
        temp_match = _YT_TEMP_MERGE_REGEX.search(name)
        if temp_match:
            return FilterResult(
                is_transient=True,
                source_platform=self.source_id,
                reason="yt-dlp 音视频合并过渡文件 (.temp)",
            )

        return None
