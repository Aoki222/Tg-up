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


class NoVideoStreamError(RuntimeError):
    """媒体文件中无视频流，无法提取画面截图。"""


async def run_ffmpeg_command(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
    """异步跑一条 ffmpeg；CancelledError 时 kill 子进程。

    抽一帧用不到多核。不限制的话 ffmpeg 会占满 2 核，上传的事件循环抢不到 CPU。
    """
    if "-threads" not in arguments:
        arguments = ["-threads", "1", *arguments]
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
        if "does not contain any stream" in err:
            logger.warning("媒体文件中无可用视频流，无法截图: %s", err[-300:])
            raise NoVideoStreamError(f"媒体文件中无可用视频流: {err[-300:]}")
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
    """职责：生成高清自适应网格剧情预览图。基于 vcsi 原生自适应比例，绝不拉伸、绝不裁切画面。"""

    GRID_COLUMNS = 3
    GRID_ROWS = 5
    CELL_WIDTH = 320
    CELL_HEIGHT = 180

    def __init__(
        self,
        grid_layout: str = "3x5",
        show_timestamp: bool = True,
        show_metadata: bool = True,
        ffmpeg_path: str = "ffmpeg",
        interval_seconds: int = 60,
        ffprobe_path: str = "ffprobe",
        jpeg_quality: int = 80,
        page_dir: Path | None = None,
    ):
        self.grid_layout = grid_layout or "3x5"
        self.show_timestamp = show_timestamp
        self.show_metadata = show_metadata
        self.ffmpeg_path = ffmpeg_path
        self.interval_seconds = interval_seconds
        self.ffprobe_path = ffprobe_path
        self.jpeg_quality = jpeg_quality
        self.page_dir = page_dir

        # 解析行列数并规格化
        cols, rows = self._parse_layout(self.grid_layout)
        self.columns = cols
        self.rows = rows
        self.grid_layout = f"{cols}x{rows}"

    @staticmethod
    def _parse_layout(layout: str) -> tuple[int, int]:
        try:
            clean = layout.lower().replace("*", "x").replace("×", "x").replace(",", "x")
            parts = clean.split("x")
            c, r = int(parts[0].strip()), int(parts[1].strip())
            if 1 <= c <= 20 and 1 <= r <= 20:
                return c, r
        except Exception:
            pass
        return 3, 5

    @property
    def frame_count(self) -> int:
        return self.columns * self.rows

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
        if not frame_paths:
            raise ValueError("帧列表为空，无法拼图")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        selected_frames = list(frame_paths[: self.frame_count])

        with Image.open(selected_frames[0]) as first_img:
            src_w, src_h = first_img.size
        cell_w = self.CELL_WIDTH
        cell_h = max(10, int(src_h * cell_w / max(1, src_w)))

        grid_image = Image.new("RGB", (self.columns * cell_w, self.rows * cell_h), "black")
        for index in range(self.frame_count):
            row, column = divmod(index, self.columns)
            left, top = column * cell_w, row * cell_h
            if index < len(selected_frames):
                with Image.open(selected_frames[index]) as source_image:
                    resized = source_image.convert("RGB").resize((cell_w, cell_h), Image.Resampling.BICUBIC)
                    grid_image.paste(resized, (left, top))
        grid_image.save(output_path, "JPEG", quality=self.jpeg_quality, optimize=True)
        logger.info("拼图完成：%dx%d -> %s", self.columns, self.rows, output_path)
        return output_path

    async def _build_via_vcsi(self, video_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        py_runner = (
            "from PIL import ImageFont; "
            "hasattr(ImageFont.FreeTypeFont, 'getsize') or setattr("
            "ImageFont.FreeTypeFont, 'getsize', "
            "lambda self, text: (self.getbbox(text)[2] - self.getbbox(text)[0], self.getbbox(text)[3] - self.getbbox(text)[1])); "
            "from vcsi import vcsi; vcsi.main()"
        )
        args = [
            sys.executable,
            "-c",
            py_runner,
            str(video_path),
            "-g",
            self.grid_layout,
            "-w",
            "1200",
            "--quality",
            str(self.jpeg_quality),
            "-o",
            str(output_path),
        ]
        if self.show_timestamp:
            args.append("-t")
        if not self.show_metadata:
            args.extend(["--metadata-position", "hidden"])

        process = await asyncio.create_subprocess_exec(
            *args,
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
            if "does not contain any stream" in err:
                raise NoVideoStreamError(f"媒体文件中无可用视频流: {err[-300:]}")
            raise RuntimeError(f"vcsi 返回异常状态码 {process.returncode}: {err[-500:]}")

        if not output_path.is_file():
            raise RuntimeError(f"vcsi 未生成目标图片：{output_path}")

        logger.info("vcsi 拼图完成：%s (%s) -> %s", video_path.name, self.grid_layout, output_path)
        return output_path

    async def build_content_page_async(self, video_path: Path, output_path: Path | None = None) -> Path:
        video_path = Path(video_path)
        if not video_path.is_file():
            raise FileNotFoundError(f"视频不存在：{video_path}")
        output_path = resolve_page_output(video_path, "_content.jpg", output_path, self.page_dir)

        try:
            return await self._build_via_vcsi(video_path, output_path)
        except NoVideoStreamError:
            raise
        except Exception as err:
            logger.warning("vcsi 生成失败，回退至内置抽帧: %s", err)
            with tempfile.TemporaryDirectory(prefix="content_frames_") as temp_directory:
                frame_paths = await self.extract_interval_frames(video_path, Path(temp_directory))
                return await asyncio.to_thread(self.merge_frames_to_grid, frame_paths, output_path)




def resolve_thumb_path(video_path: Path | str, page_dir: Path | None = None) -> Path:
    return resolve_page_output(Path(video_path), "_thumb.jpg", None, page_dir)


class VideoThumbnail:
    """职责：生成轻量级视频缩略图（≤320px，用于 Telegram 视频原生内嵌 thumb）。"""

    MAX_DIMENSION = 320
    JPEG_QUALITY = 80

    def __init__(self, ffmpeg_path: str = "ffmpeg", page_dir: Path | None = None):
        self.ffmpeg_path = ffmpeg_path
        self.page_dir = page_dir

    def create_from_image(self, source_image_path: Path, output_path: Path) -> Path:
        """从已有的图片（如 _single.jpg）快速缩放生成，耗时极短（<5ms）。"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((self.MAX_DIMENSION, self.MAX_DIMENSION), Image.Resampling.LANCZOS)
            img.save(output_path, "JPEG", quality=self.JPEG_QUALITY, optimize=True)
        logger.info("已生成视频缩略图（从现有图片缩放）: %s -> %s", source_image_path, output_path)
        return output_path

    async def extract_from_video(self, video_path: Path, output_path: Path | None = None) -> Path:
        """直接从视频第 1 秒抽取 1 帧并限制最大分辨率为 320。"""
        video_path = Path(video_path)
        output_path = resolve_page_output(video_path, "_thumb.jpg", output_path, self.page_dir)
        if not video_path.is_file():
            raise FileNotFoundError(f"视频不存在：{video_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        vf_filter = (
            f"scale=w={self.MAX_DIMENSION}:h={self.MAX_DIMENSION}:"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
        await run_ffmpeg_command(
            [
                "-ss",
                "1",
                "-i",
                str(video_path),
                "-vframes",
                "1",
                "-vf",
                vf_filter,
                "-q:v",
                "4",
                str(output_path),
            ],
            self.ffmpeg_path,
        )
        if not output_path.is_file():
            raise RuntimeError(f"缩略图未生成：{output_path}")
        logger.info("单帧缩略图提取完成：%s -> %s", video_path, output_path)
        return output_path

    async def ensure_thumbnail_async(self, video_path: Path, existing_page_path: Path | None = None) -> Path | None:
        """确保生成 _thumb.jpg：若已有 _single.jpg 则无损缩放，否则直接提取。"""
        video_path = Path(video_path)
        output_path = resolve_page_output(video_path, "_thumb.jpg", None, self.page_dir)
        if output_path.is_file():
            return output_path

        if existing_page_path and Path(existing_page_path).is_file():
            if str(existing_page_path).endswith("_single.jpg"):
                try:
                    return await asyncio.to_thread(self.create_from_image, Path(existing_page_path), output_path)
                except Exception as err:
                    logger.warning("从单帧图生成缩略图失败，尝试直接从视频提取: %s", err)

        try:
            return await self.extract_from_video(video_path, output_path)
        except Exception as err:
            logger.warning("从视频提取缩略图失败: %s", err)
            return None
