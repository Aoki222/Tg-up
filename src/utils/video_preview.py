"""封面生成，只落盘，不写数据库。路径回填由 FileIngestor.update_preview 负责。

ffmpeg/ffprobe 用 asyncio.create_subprocess_exec，取消时 kill 子进程。
PIL 拼图仍是 CPU 阻塞，丢 to_thread。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from PIL import Image

from ..config import PAGE_DIR
from ..logger import get_logger

logger = get_logger(__name__)

_CREATE_NO_WINDOW = 0x08000000


def grid_seek_timestamps(duration: float, count: int) -> list[float]:
    """把时长均分成 count 段，取每段中点，避开片头片尾。"""
    if duration <= 0 or count <= 0:
        return []
    return [duration * (index + 0.5) / count for index in range(count)]


def interval_seek_timestamps(interval_seconds: float, count: int, start: float = 0.5) -> list[float]:
    """读不到时长时：从 start 起按固定间隔 seek，最多 count 张。"""
    if interval_seconds <= 0 or count <= 0:
        return []
    return [start + index * interval_seconds for index in range(count)]


def resolve_page_output(
    video_path: Path,
    suffix: str,
    output_path: Path | None = None,
    page_dir: Path | None = None,
) -> Path:
    if output_path is None:
        output_path = (page_dir or PAGE_DIR) / f"{Path(video_path).stem}{suffix}"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def _subprocess_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except (TimeoutError, asyncio.CancelledError):
        pass


async def run_ffmpeg_command(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
    """异步跑一条 ffmpeg；CancelledError 时 kill 子进程。"""
    command = [ffmpeg_path, "-y", *arguments]
    logger.info("执行 ffmpeg：%s", " ".join(command))
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_subprocess_kwargs(),
    )
    try:
        _stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        await _kill_process(process)
        raise
    if process.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace")
        logger.error("ffmpeg 失败：%s", err[-2000:])
        raise RuntimeError(f"ffmpeg 失败：{err[-500:]}")


class FirstFramePreview:
    """职责：截视频第 1 秒画面存为单图，返回落盘路径（page_path 待写库）。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg", page_dir: Path | None = None):
        self.ffmpeg_path = ffmpeg_path
        self.page_dir = page_dir

    async def extract_first_frame_async(self, video_path: Path, output_path: Path | None = None) -> Path:
        video_path = Path(video_path)
        output_path = resolve_page_output(video_path, "_single.jpg", output_path, self.page_dir)
        if not video_path.is_file():
            raise FileNotFoundError(f"视频不存在：{video_path}")
        await run_ffmpeg_command(
            ["-ss", "1", "-i", str(video_path), "-vframes", "1", str(output_path)],
            self.ffmpeg_path,
        )
        if not output_path.is_file():
            raise RuntimeError(f"截图未生成：{output_path}")
        logger.info("单帧截图完成：%s -> %s", video_path, output_path)
        return output_path


class GridPreview:
    """职责：抽 15 帧拼成固定 3 列 x 5 行小体积网格图，返回落盘路径。"""

    GRID_COLUMNS = 3
    GRID_ROWS = 5
    CELL_WIDTH = 320
    CELL_HEIGHT = 180

    def __init__(self, ffmpeg_path: str = "ffmpeg", interval_seconds: int = 60,
                 ffprobe_path: str = "ffprobe", jpeg_quality: int = 70, page_dir: Path | None = None):
        self.ffmpeg_path = ffmpeg_path
        self.interval_seconds = interval_seconds
        self.ffprobe_path = ffprobe_path
        self.jpeg_quality = jpeg_quality
        self.page_dir = page_dir

    @property
    def frame_count(self) -> int:
        return self.GRID_COLUMNS * self.GRID_ROWS

    async def get_video_duration(self, video_path: Path) -> float | None:
        process = await asyncio.create_subprocess_exec(
            self.ffprobe_path, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_subprocess_kwargs(),
        )
        try:
            stdout, _stderr = await process.communicate()
        except asyncio.CancelledError:
            await _kill_process(process)
            raise
        try:
            return float((stdout or b"").decode().strip())
        except Exception:
            logger.warning("ffprobe 读时长失败，走间隔抽帧：%s", video_path)
            return None

    async def _seek_one_frame(self, video_path: Path, timestamp: float, output_path: Path) -> Path | None:
        """-ss 在 -i 前：输入侧随机读，落到时间点之前最近的关键帧。"""
        try:
            await run_ffmpeg_command(
                [
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "4",
                    str(output_path),
                ],
                self.ffmpeg_path,
            )
        except RuntimeError:
            logger.warning("关键帧 seek 失败 t=%.3f: %s", timestamp, video_path)
            return None
        return output_path if output_path.is_file() else None

    async def extract_interval_frames(self, video_path: Path, frame_directory: Path) -> list[Path]:
        video_path = Path(video_path)
        frame_directory = Path(frame_directory)
        if not video_path.is_file():
            raise FileNotFoundError(f"视频不存在：{video_path}")
        frame_directory.mkdir(parents=True, exist_ok=True)
        duration = await self.get_video_duration(video_path)
        if duration and duration > 0:
            timestamps = grid_seek_timestamps(duration, self.frame_count)
        else:
            timestamps = interval_seek_timestamps(self.interval_seconds, self.frame_count)

        frame_paths: list[Path] = []
        for index, timestamp in enumerate(timestamps, start=1):
            output_path = frame_directory / f"frame_{index:04d}.jpg"
            grabbed = await self._seek_one_frame(video_path, timestamp, output_path)
            if grabbed is not None:
                frame_paths.append(grabbed)

        if not frame_paths:
            fallback = frame_directory / "frame_0001.jpg"
            grabbed = await self._seek_one_frame(video_path, 1.0, fallback)
            if grabbed is not None:
                frame_paths = [grabbed]
        if not frame_paths:
            raise RuntimeError(f"未抽到任何帧：{video_path}")
        logger.info("抽帧完成：%s 共 %s 张", video_path, len(frame_paths))
        return frame_paths

    def merge_frames_to_grid(self, frame_paths: list[Path], output_path: Path) -> Path:
        from PIL import ImageOps

        if not frame_paths:
            raise ValueError("帧列表为空，无法拼图")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        selected_frames = list(frame_paths[: self.frame_count])
        grid_image = Image.new("RGB", (self.GRID_COLUMNS * self.CELL_WIDTH, self.GRID_ROWS * self.CELL_HEIGHT), "black")
        for index in range(self.frame_count):
            row, column = divmod(index, self.GRID_COLUMNS)
            left, top = column * self.CELL_WIDTH, row * self.CELL_HEIGHT
            if index < len(selected_frames):
                with Image.open(selected_frames[index]) as source_image:
                    grid_image.paste(
                        ImageOps.fit(source_image.convert("RGB"), (self.CELL_WIDTH, self.CELL_HEIGHT)),
                        (left, top),
                    )
        grid_image.save(output_path, "JPEG", quality=self.jpeg_quality, optimize=True)
        logger.info("拼图完成：3x5 -> %s", output_path)
        return output_path

    async def build_content_page_async(self, video_path: Path, output_path: Path | None = None) -> Path:
        video_path = Path(video_path)
        output_path = resolve_page_output(video_path, "_content.jpg", output_path, self.page_dir)
        with tempfile.TemporaryDirectory(prefix="content_frames_") as temp_directory:
            frame_paths = await self.extract_interval_frames(video_path, Path(temp_directory))
            return await asyncio.to_thread(self.merge_frames_to_grid, frame_paths, output_path)
