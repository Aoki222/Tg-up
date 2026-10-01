"""通用操作系统与常见下载工具的静态后缀表。"""
from __future__ import annotations

# O(1) 静态后缀映射表: suffix -> (source_id, 拦截原因)
GENERIC_SUFFIX_MAP: dict[str, tuple[str, str]] = {
    ".part": ("generic", "未完成下载分块 (.part)"),
    ".crdownload": ("generic", "Chrome 下载未完成 (.crdownload)"),
    ".aria2": ("generic", "Aria2 控制文件 (.aria2)"),
    ".downloading": ("generic", "通用下载中后缀 (.downloading)"),
    ".tmp": ("generic", "系统/应用临时文件 (.tmp)"),
    ".temp": ("generic", "通用临时文件 (.temp)"),
    ".bak": ("generic", "备份文件 (.bak)"),
    ".ytdl": ("youtube", "yt-dlp 专有断点文件 (.ytdl)"),
}
