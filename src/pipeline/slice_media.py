"""把一个视频无损切成指定段数。

只复制第一条视频轨和第一条音轨。段数对不上或某一段仍超过机器人上限时，
调用方删掉成品并告诉用户，不会偷偷改段数。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..domain.slices import (
    MSG_EMPTY,
    MSG_NO_DURATION,
    MSG_NO_VIDEO,
    MSG_TOO_DENSE,
    SLICE_MAX_PARTS,
    container_extension,
    exceeds_bot_limit,
    msg_keyframes,
    msg_need_parts,
    parts_to_fit,
    segment_filename,
)
from ..logger import get_logger
from ..utils.video_preview import _kill_process, _subprocess_kwargs, run_ffmpeg_command

logger = get_logger(__name__)


class SliceCutError(RuntimeError):
    """切片不能按用户要的段数完成。message 直接写到卡片上。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ProbeInfo:
    duration: float | None
    video_codec: str | None
    audio_codec: str | None


def directory_has_room(directory: Path, need_bytes: int) -> bool:
    """剩余空间至少再容得下一份源文件。"""
    directory.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(directory).free >= max(0, int(need_bytes))


async def probe_media(source: Path) -> ProbeInfo:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name",
        "-of",
        "json",
        str(source),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_subprocess_kwargs(),
    )
    try:
        stdout, _stderr = await process.communicate()
    except asyncio.CancelledError:
        await _kill_process(process)
        raise
    if process.returncode != 0:
        return ProbeInfo(None, None, None)
    try:
        payload = json.loads((stdout or b"").decode("utf-8"))
    except json.JSONDecodeError:
        return ProbeInfo(None, None, None)
    duration: float | None
    try:
        duration = float((payload.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    video_codec = None
    audio_codec = None
    for stream in payload.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        kind = stream.get("codec_type")
        codec = stream.get("codec_name")
        if kind == "video" and video_codec is None and codec:
            video_codec = str(codec)
        elif kind == "audio" and audio_codec is None and codec:
            audio_codec = str(codec)
    return ProbeInfo(duration, video_codec, audio_codec)


def _concat_list(paths: list[Path], list_path: Path) -> None:
    lines = []
    for path in paths:
        escaped = path.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _merge_tail(files: list[Path], parts: int, directory: Path, extension: str) -> list[Path]:
    """分段器多切出来的尾巴并回最后一段，使文件数等于用户要的段数。"""
    head = files[: parts - 1]
    tail = files[parts - 1 :]
    list_path = directory / "concat.txt"
    merged = directory / f"merged{extension}"
    _concat_list(tail, list_path)
    await run_ffmpeg_command(
        ["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(merged)]
    )
    for path in tail:
        path.unlink(missing_ok=True)
    list_path.unlink(missing_ok=True)
    return head + [merged]


async def _faststart(path: Path) -> None:
    temp = path.with_name(path.name + ".fast")
    try:
        await run_ffmpeg_command(
            ["-i", str(path), "-c", "copy", "-movflags", "+faststart", str(temp)]
        )
    except Exception:
        temp.unlink(missing_ok=True)
        logger.warning("faststart 失败，保留原分段: %s", path)
        return
    if not temp.is_file():
        return
    path.unlink(missing_ok=True)
    temp.replace(path)


async def cut_into_parts(source: Path, directory: Path, parts: int) -> list[tuple[Path, int]]:
    """切成恰好 parts 段。失败抛 SliceCutError，目录里的半成品由调用方删除。"""
    info = await probe_media(source)
    if info.duration is None or info.duration <= 0:
        raise SliceCutError(MSG_NO_DURATION)
    if not info.video_codec:
        raise SliceCutError(MSG_NO_VIDEO)
    extension = container_extension(info.video_codec, info.audio_codec)
    directory.mkdir(parents=True, exist_ok=True)
    pattern = directory / f"seg-%02d{extension}"
    segment_time = info.duration / parts
    arguments = ["-i", str(source), "-map", "0:v:0"]
    if info.audio_codec:
        arguments.extend(["-map", "0:a:0"])
    arguments.extend(
        [
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            f"{segment_time:.3f}",
            "-reset_timestamps",
            "1",
            "-segment_start_number",
            "1",
            str(pattern),
        ]
    )
    await run_ffmpeg_command(arguments)
    files = sorted(directory.glob(f"seg-*{extension}"))
    if not files:
        raise SliceCutError(MSG_EMPTY)
    if len(files) > parts:
        files = await _merge_tail(files, parts, directory, extension)
    if len(files) != parts:
        raise SliceCutError(msg_keyframes(len(files)))
    if extension == ".mp4":
        for path in files:
            await _faststart(path)
    sizes = [path.stat().st_size for path in files]
    if any(exceeds_bot_limit(size) for size in sizes):
        needed = parts_to_fit(parts, max(sizes))
        if needed > SLICE_MAX_PARTS:
            raise SliceCutError(MSG_TOO_DENSE)
        raise SliceCutError(msg_need_parts(needed))
    renamed: list[tuple[Path, int]] = []
    for index, path in enumerate(files, start=1):
        target = directory / segment_filename(source, index, parts, extension)
        if path.resolve() != target.resolve():
            path.replace(target)
        renamed.append((target, target.stat().st_size))
    return renamed


def remove_tree(directory: Path) -> None:
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)