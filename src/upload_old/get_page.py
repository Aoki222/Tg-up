"""视频截图：用 ffmpeg 截取视频第一秒的画面。

提供 extract_first_frame()，输入视频路径，返回截图图片路径。
截图默认保存到 uploader/page/ 目录。
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from ..config import config
from ..logger import get_logger

logger = get_logger(__name__)

# 截图默认保存目录（来自 .env 的 PAGE_DIR）
_DEFAULT_OUTPUT_DIR = config.page_dir

# ffmpeg 可执行文件名（依赖系统 PATH 中的 ffmpeg）
_FFMPEG = "ffmpeg"

# 默认截取时间点：第一秒
_DEFAULT_SEEK_SECONDS = 1.0

# 截图格式
_OUTPUT_EXT = ".jpg"


def extract_first_frame(
    video_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    seek_seconds: float = _DEFAULT_SEEK_SECONDS,
    overwrite: bool = False,
) -> Path:
    """用 ffmpeg 截取视频第一秒的画面并返回截图路径。

    Args:
        video_path: 输入视频文件路径。
        output_dir: 截图保存目录；缺省为 uploader/page。
        seek_seconds: 截取时间点（秒），默认 1 秒。
        overwrite: 若目标截图已存在，是否重新生成。默认 False（复用已有截图）。

    Returns:
        截图图片的绝对路径。

    Raises:
        FileNotFoundError: 输入视频不存在。
        RuntimeError: ffmpeg 执行失败。
    """
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"视频文件不存在: {video}")

    out_dir = Path(output_dir) if output_dir is not None else _DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    output = _build_output_path(video, out_dir)

    if output.exists() and not overwrite:
        logger.info("截图已存在，直接复用: %s", output)
        return output

    _run_ffmpeg(video, output, seek_seconds)
    logger.info("已截取 %s 第 %.1fs 画面 -> %s", video, seek_seconds, output)
    return output


def _build_output_path(video: Path, out_dir: Path) -> Path:
    """生成输出图片路径：<视频名>_page_<时间戳>.jpg（同名视频多次调用不冲突）。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return out_dir / f"{video.stem}_page_{stamp}{_OUTPUT_EXT}"


def _run_ffmpeg(video: Path, output: Path, seek_seconds: float) -> None:
    """执行 ffmpeg 截帧命令。"""
    cmd = [
        _FFMPEG,
        "-y",
        "-ss", str(seek_seconds),
        "-i", str(video),
        "-frames:v", "1",
        "-q:v", "2",
        str(output),
    ]
    logger.debug("执行 ffmpeg: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError("未找到 ffmpeg，请先安装（Ubuntu: sudo apt install ffmpeg）") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 截帧失败（returncode={result.returncode}）\n"
            f"stderr: {result.stderr.strip()}"
        )
    if not output.exists():
        raise RuntimeError(f"ffmpeg 执行成功但未生成文件: {output}")
