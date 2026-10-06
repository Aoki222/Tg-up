"""过大视频切片的纯计算。

段数下限按文件大小除以切片目标，保证平均下来每段低于机器人上限。
真正切开、入库、上传不在这里。
"""

from __future__ import annotations

import math
from pathlib import Path

from .limits import TELEGRAM_BOT_MAX_BYTES

# 比机器人上限留出余量，用来吃掉关键帧对不齐多出来的尾巴。
SLICE_TARGET_BYTES = 1_782_000_000
SLICE_MAX_PARTS = 30

SLICE_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".flv", ".ts", ".mpeg", ".mpg"}
)

MSG_WAIT = "等待切片"
MSG_CUTTING = "正在切片"
MSG_INTERRUPTED = "切片中断"
MSG_NO_DURATION = "读不到时长，无法按大小切片"
MSG_NO_VIDEO = "读不到视频轨，无法切片"
MSG_NO_DISK = "磁盘空间不足，无法切片"
MSG_TOO_DENSE = "关键帧间隔太长，30 段内仍超过 2GB"
MSG_EMPTY = "切片未生成文件"

_BLOCKED_EXACT = frozenset(
    {
        MSG_INTERRUPTED,
        MSG_NO_DURATION,
        MSG_NO_VIDEO,
        MSG_NO_DISK,
        MSG_TOO_DENSE,
        MSG_EMPTY,
    }
)


def is_sliceable_video(path: str | Path) -> bool:
    """只有常见视频后缀可以切片。压缩包和镜像仍走个人号。"""
    return Path(path).suffix.lower() in SLICE_VIDEO_SUFFIXES


def minimum_slice_parts(file_size: int) -> int:
    """按时长平均切时，至少要这么多段才有希望每段低于机器人上限。"""
    size = max(0, int(file_size))
    if size <= 0:
        return 1
    return max(1, math.ceil(size / SLICE_TARGET_BYTES))


def parts_to_fit(requested: int, largest_part: int) -> int:
    """某一段超限时，按最大那段的体积反推至少要几段。"""
    requested = max(1, int(requested))
    largest = max(0, int(largest_part))
    if largest <= SLICE_TARGET_BYTES:
        return requested
    scaled = math.ceil(requested * largest / SLICE_TARGET_BYTES)
    return max(scaled, requested + 1)


def segment_filename(source: Path, index: int, count: int, extension: str) -> str:
    """上传时 Telegram 看到的文件名，带段号。"""
    suffix = extension if extension.startswith(".") else f".{extension}"
    return f"{source.stem}.part{index:02d}-of-{count:02d}{suffix}"


def part_caption(caption: str, index: int, count: int) -> str:
    """每段说明末尾带（2/6）。正文太长时先截正文，段号留在 1024 字以内。"""
    mark = f"（{index}/{count}）"
    base = (caption or "").strip()
    if not base:
        return mark[:1024]
    suffix = f"\n{mark}"
    room = 1024 - len(suffix)
    if room <= 0:
        return mark[:1024]
    return f"{base[:room]}{suffix}"


def container_extension(video_codec: str | None, audio_codec: str | None) -> str:
    """常见的 H.264/H.265 配 AAC 进 mp4，其余用 mkv，都是复制不重编码。"""
    video = (video_codec or "").lower()
    audio = (audio_codec or "").lower()
    if video in {"h264", "hevc", "av1", "mpeg4"} and audio in {"", "aac", "mp3"}:
        return ".mp4"
    return ".mkv"


def msg_released(count: int) -> str:
    """源文件卡片：各段已经进入正常的封面和上传队列。"""
    return f"已分成 {int(count)} 段，分段已进入队列"


def msg_part(index: int, count: int) -> str:
    return f"第 {index}/{count} 段"


def msg_part_failed(index: int, count: int) -> str:
    return f"第 {index}/{count} 段上传失败"


def msg_need_parts(count: int) -> str:
    return f"某一段仍超过 2GB，至少要 {count} 段"


def msg_keyframes(count: int) -> str:
    return f"关键帧间隔太长，最多切成 {count} 段"


def slice_phase(error: str | None) -> str:
    """由过大任务的 error_msg 推出卡片阶段。

    idle / queued / cutting / released / uploading / failed / blocked
    """
    text = (error or "").strip()
    if text == MSG_WAIT:
        return "queued"
    if text == MSG_CUTTING:
        return "cutting"
    if text.startswith("已分成 ") and text.endswith("段，分段已进入队列"):
        return "released"
    if text.startswith("第 ") and text.endswith("段上传失败"):
        return "failed"
    if text.startswith("第 ") and text.endswith("段"):
        return "uploading"
    if text in _BLOCKED_EXACT or text.startswith("某一段仍超过") or text.startswith("关键帧间隔太长"):
        return "blocked"
    return "idle"


def user_dispatch_blocked(error: str | None) -> bool:
    """切片已经开始或某一段失败时，不再整文件交给个人号。"""
    return slice_phase(error) in {"queued", "cutting", "released", "uploading", "failed"}


def exceeds_bot_limit(size: int) -> bool:
    return int(size) > TELEGRAM_BOT_MAX_BYTES
