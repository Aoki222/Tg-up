"""封面生成，只落盘，不写数据库。路径回填由 FileIngestor.update_preview 负责。

ffmpeg/ffprobe 用 asyncio.create_subprocess_exec，取消时 kill 子进程。
PIL 拼图仍是 CPU 阻塞，丢 to_thread。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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


# 整张封面的目标宽度。格子按画面比例缩放，不裁切。
SHEET_WIDTH = 1200
GRID_GAP = 4
# 一次 ffmpeg 里塞的 seek 数。再大的话，Windows 命令行会顶到 32KB。
GRID_SEEK_BATCH = 8


# VP9/AV1 和高于 1080p 的片子，解码器默认会吃满所有核，而且 -ss 会从上一张
# 关键帧一直解到目标时间。4K WebM 这样一轮 3x3 能占满 1~2 分钟。
_HEAVY_CODECS = frozenset({"vp8", "vp9", "av1", "av01"})
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


@dataclass(frozen=True)
class VideoProbe:
    duration: float | None
    display_width: int
    display_height: int
    size_bytes: int | None
    codec: str = ""


def preview_decode_is_heavy(probe: VideoProbe) -> bool:
    """这类片子一次只解一个关键帧，避免和上传抢 CPU。"""
    if (probe.codec or "").strip().lower() in _HEAVY_CODECS:
        return True
    return probe.display_width > 1920 or probe.display_height > 1080


def cell_pixel_size(
    sheet_width: int,
    columns: int,
    src_width: int,
    src_height: int,
    gap: int = GRID_GAP,
) -> tuple[int, int]:
    """按源画面比例算单格大小。宽高都取偶数，方便 yuv420 编码。"""
    columns = max(1, columns)
    cell_w = max(2, (sheet_width - gap * (columns - 1)) // columns)
    if cell_w % 2:
        cell_w -= 1
    src_width = src_width if src_width > 0 else 16
    src_height = src_height if src_height > 0 else 9
    cell_h = max(2, int(src_height * cell_w / src_width))
    if cell_h % 2:
        cell_h -= 1
    return max(2, cell_w), max(2, cell_h)


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_bytes(num: int | None) -> str:
    if num is None or num < 0:
        return ""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)}B"
            return f"{value:.1f}{unit}"
        value /= 1024
    return ""


def _stream_rotation(stream: dict) -> int:
    tags = stream.get("tags") or {}
    raw = tags.get("rotate")
    if raw is None:
        for side in stream.get("side_data_list") or []:
            if "rotation" in side:
                raw = side.get("rotation")
                break
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _apply_sample_aspect(width: int, height: int, sar: str | None) -> tuple[int, int]:
    if not sar or sar in {"1:1", "0:1", "N/A"}:
        return width, height
    try:
        num, den = sar.split(":", 1)
        num_i, den_i = int(num), int(den)
    except ValueError:
        return width, height
    if num_i <= 0 or den_i <= 0:
        return width, height
    return max(1, int(width * num_i / den_i)), height


def _read_duration(stream: dict, fmt: dict) -> float | None:
    for source in (stream, fmt):
        try:
            value = float(source.get("duration"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _load_font(size: int):
    candidates: list[Path] = []
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        candidates.extend(
            [
                windir / "Fonts" / "msyh.ttc",
                windir / "Fonts" / "simsun.ttc",
                windir / "Fonts" / "msgothic.ttc",
                windir / "Fonts" / "arial.ttf",
            ]
        )
    candidates.extend(
        [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        ]
    )
    for path in candidates:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fit_text(text: str, font, max_width: int) -> str:
    if max_width <= 0:
        return text
    try:
        if font.getlength(text) <= max_width:
            return text
        ellipsis = "..."
        trimmed = text
        while trimmed and font.getlength(trimmed + ellipsis) > max_width:
            trimmed = trimmed[:-1]
        return (trimmed + ellipsis) if trimmed else ellipsis
    except Exception:
        return text[:80]


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
    """封面进程让出 CPU。Windows 用低于正常优先级，Linux 在命令前加 nice。"""
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW | _BELOW_NORMAL_PRIORITY_CLASS}
    return {}


def _priority_argv(command: list[str]) -> list[str]:
    if sys.platform == "win32":
        return command
    return ["nice", "-n", "19", *command]


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

    抽一帧用不到多核。不限制的话 VP9/AV1 会把 2 核占满，上传的事件循环和
    cryptg 都抢不到 CPU。线程锁死为 1，进程优先级放低，上传忙时封面让路。
    """
    if "-threads" not in arguments:
        arguments = ["-threads", "1", *arguments]
    if "-filter_threads" not in arguments:
        arguments = ["-filter_threads", "1", *arguments]
    command = _priority_argv([ffmpeg_path, "-y", *arguments])
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
    """职责：生成自适应比例的网格封面。按画面比例缩放，不拉伸、不裁切。

    1080p 及以下的普通编码仍按批 seek。VP9、AV1 和高于 1080p 的片子改成一次只解
    一张关键帧：不从关键帧再解码到精确时间点，也不并行开多路解码。
    """

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
            *_priority_argv([
                self.ffprobe_path, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
            ]),
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

    async def probe_video(self, video_path: Path) -> VideoProbe:
        """读时长、显示分辨率和文件大小。封面图和抽帧共用这一次探测。"""
        process = await asyncio.create_subprocess_exec(
            *_priority_argv([
                self.ffprobe_path,
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(video_path),
            ]),
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
            payload = json.loads((stdout or b"").decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            payload = {}
        video_stream = None
        for stream in payload.get("streams") or []:
            if stream.get("codec_type") != "video":
                continue
            if (stream.get("disposition") or {}).get("attached_pic"):
                continue
            video_stream = stream
            break
        if video_stream is None:
            raise NoVideoStreamError(f"媒体文件中无可用视频流: {video_path}")
        try:
            width = int(video_stream.get("width") or 0)
            height = int(video_stream.get("height") or 0)
        except (TypeError, ValueError):
            width, height = 0, 0
        if abs(_stream_rotation(video_stream)) % 180 == 90:
            width, height = height, width
        width, height = _apply_sample_aspect(width, height, video_stream.get("sample_aspect_ratio"))
        if width <= 0 or height <= 0:
            width, height = 16, 9
        fmt = payload.get("format") or {}
        try:
            size_bytes = int(fmt["size"]) if fmt.get("size") is not None else None
        except (TypeError, ValueError, KeyError):
            size_bytes = None
        return VideoProbe(
            duration=_read_duration(video_stream, fmt),
            display_width=width,
            display_height=height,
            size_bytes=size_bytes,
            codec=str(video_stream.get("codec_name") or ""),
        )

    def _timestamps_for(self, probe: VideoProbe) -> list[float]:
        if probe.duration and probe.duration > 0:
            return grid_seek_timestamps(probe.duration, self.frame_count)
        return interval_seek_timestamps(self.interval_seconds, self.frame_count)

    async def _grab_frame(
        self,
        video_path: Path,
        timestamp: float,
        dest: Path,
        scale: str,
        keyframe_only: bool,
    ) -> bool:
        """抽一张已经缩好的图。重片子先停在关键帧上，失败再退回精确 seek。"""
        attempts = []
        if keyframe_only:
            attempts.append(["-threads", "1", "-noaccurate_seek", "-ss", f"{timestamp:.3f}", "-i", str(video_path)])
        attempts.append(["-threads", "1", "-ss", f"{timestamp:.3f}", "-i", str(video_path)])
        for inputs in attempts:
            if dest.exists():
                dest.unlink()
            args = [
                "-hide_banner", "-loglevel", "error", "-filter_threads", "1",
                *inputs,
                "-frames:v", "1",
                "-vf", scale,
                "-q:v", "4",
                str(dest),
            ]
            try:
                await run_ffmpeg_command(args, self.ffmpeg_path)
            except NoVideoStreamError:
                raise
            except RuntimeError as error:
                logger.warning("抽帧失败 t=%.3f：%s", timestamp, error)
                continue
            if dest.is_file() and dest.stat().st_size > 0:
                return True
        return False

    async def _extract_keyframes(
        self,
        video_path: Path,
        frame_directory: Path,
        timestamps: list[float],
        cell_w: int,
        cell_h: int,
    ) -> list[tuple[Path, float]]:
        """一次只跑一个 ffmpeg，只解 seek 落到的那张关键帧。"""
        frame_directory.mkdir(parents=True, exist_ok=True)
        found: list[tuple[Path, float]] = []
        scale = f"scale={cell_w}:{cell_h}:flags=bilinear"
        for index, timestamp in enumerate(timestamps, start=1):
            dest = frame_directory / f"frame_{index:04d}.jpg"
            if await self._grab_frame(video_path, timestamp, dest, scale, keyframe_only=True):
                found.append((dest, timestamp))
        if not found:
            raise RuntimeError(f"未抽到任何帧：{video_path}")
        return found

    async def _extract_scaled_frames(
        self,
        video_path: Path,
        frame_directory: Path,
        timestamps: list[float],
        cell_w: int,
        cell_h: int,
        heavy: bool = False,
    ) -> list[tuple[Path, float]]:
        """普通片子一条 ffmpeg 抽一批。重片子走 _extract_keyframes。"""
        if heavy:
            return await self._extract_keyframes(
                video_path, frame_directory, timestamps, cell_w, cell_h
            )
        frame_directory.mkdir(parents=True, exist_ok=True)
        found: list[tuple[Path, float]] = []
        scale = f"scale={cell_w}:{cell_h}:flags=bilinear"
        for offset in range(0, len(timestamps), GRID_SEEK_BATCH):
            batch = timestamps[offset:offset + GRID_SEEK_BATCH]
            args = ["-hide_banner", "-loglevel", "error"]
            outputs: list[tuple[Path, float]] = []
            for index, timestamp in enumerate(batch):
                dest = frame_directory / f"frame_{offset + index + 1:04d}.jpg"
                outputs.append((dest, timestamp))
                args.extend(["-threads", "1", "-ss", f"{timestamp:.3f}", "-i", str(video_path)])
            for map_index, (dest, _timestamp) in enumerate(outputs):
                args.extend([
                    "-map", f"{map_index}:v:0",
                    "-frames:v", "1",
                    "-vf", scale,
                    "-q:v", "4",
                    str(dest),
                ])
            try:
                await run_ffmpeg_command(args, self.ffmpeg_path)
            except NoVideoStreamError:
                raise
            except RuntimeError as error:
                logger.warning("这一批抽帧失败，已有 %s 张：%s", len(found), error)
                break
            for dest, timestamp in outputs:
                if dest.is_file():
                    found.append((dest, timestamp))
        if not found:
            raise RuntimeError(f"未抽到任何帧：{video_path}")
        return found

    async def _seek_one_frame(self, video_path: Path, timestamp: float, output_path: Path) -> Path | None:
        """-ss 在 -i 前：输入侧随机读，落到时间点之前最近的关键帧。"""
        try:
            await run_ffmpeg_command(
                [
                    "-noaccurate_seek",
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

    def merge_frames_to_grid(
        self,
        frame_paths: list[Path],
        output_path: Path,
        timestamps: list[float] | None = None,
        metadata_lines: list[str] | None = None,
        cell_size: tuple[int, int] | None = None,
    ) -> Path:
        if not frame_paths:
            raise ValueError("帧列表为空，无法拼图")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        selected_frames = list(frame_paths[: self.frame_count])
        if cell_size is None:
            with Image.open(selected_frames[0]) as first_img:
                src_w, src_h = first_img.size
            cell_w, cell_h = cell_pixel_size(SHEET_WIDTH, self.columns, src_w, src_h)
        else:
            cell_w, cell_h = cell_size

        lines = [line for line in (metadata_lines or []) if line]
        font = _load_font(16) if lines or timestamps else None
        header_h = (8 + len(lines) * 22 + 6) if lines else 0
        sheet_w = self.columns * cell_w + GRID_GAP * (self.columns - 1)
        sheet_h = header_h + self.rows * cell_h + GRID_GAP * (self.rows - 1)
        grid_image = Image.new("RGB", (sheet_w, sheet_h), "black")
        draw = ImageDraw.Draw(grid_image) if font is not None else None
        if draw is not None and lines and font is not None:
            for index, line in enumerate(lines):
                draw.text((10, 8 + index * 22), _fit_text(line, font, sheet_w - 20), fill="white", font=font)

        stamp_font = _load_font(14) if timestamps else None
        for index in range(self.frame_count):
            row, column = divmod(index, self.columns)
            left = column * (cell_w + GRID_GAP)
            top = header_h + row * (cell_h + GRID_GAP)
            if index >= len(selected_frames):
                continue
            with Image.open(selected_frames[index]) as source_image:
                frame = source_image.convert("RGB")
                if frame.size != (cell_w, cell_h):
                    frame = frame.resize((cell_w, cell_h), Image.Resampling.BILINEAR)
                grid_image.paste(frame, (left, top))
            if draw is None or stamp_font is None or timestamps is None or index >= len(timestamps):
                continue
            label = format_clock(timestamps[index])
            right, bottom = left + cell_w - 6, top + cell_h - 4
            box = draw.textbbox((right, bottom), label, font=stamp_font, anchor="rb")
            draw.rectangle((box[0] - 3, box[1] - 1, box[2] + 3, box[3] + 1), fill=(0, 0, 0))
            draw.text((right, bottom), label, fill="white", font=stamp_font, anchor="rb")
        # optimize 会再扫一遍霍夫曼表。封面要上传，省这几百毫秒。
        grid_image.save(output_path, "JPEG", quality=self.jpeg_quality)
        logger.info("拼图完成：%dx%d -> %s", self.columns, self.rows, output_path)
        return output_path

    def _metadata_lines(self, video_path: Path, probe: VideoProbe) -> list[str]:
        parts = [f"{probe.display_width}x{probe.display_height}"]
        if probe.duration:
            parts.append(format_clock(probe.duration))
        size_text = format_bytes(probe.size_bytes)
        if size_text:
            parts.append(size_text)
        return [video_path.name, "  ".join(parts)]

    async def _write_thumb_from_frame(self, video_path: Path, frame_path: Path) -> None:
        """用已经抽到的一格做 Telegram 缩略图，避免封面做完再 seek 一次。"""
        thumb_path = resolve_thumb_path(video_path, self.page_dir)
        if thumb_path.is_file():
            return
        try:
            await asyncio.to_thread(
                VideoThumbnail(page_dir=self.page_dir).create_from_image,
                frame_path,
                thumb_path,
            )
        except Exception:
            logger.debug("用网格帧生成缩略图失败", exc_info=True)

    async def _build_fast(self, video_path: Path, output_path: Path) -> Path:
        probe = await self.probe_video(video_path)
        timestamps = self._timestamps_for(probe)
        cell_w, cell_h = cell_pixel_size(
            SHEET_WIDTH, self.columns, probe.display_width, probe.display_height
        )
        heavy = preview_decode_is_heavy(probe)
        logger.info(
            "网格抽帧：%s %s 张 %sx%s %s %s",
            video_path.name, len(timestamps), cell_w, cell_h,
            probe.codec or "unknown",
            "关键帧" if heavy else "批量",
        )
        with tempfile.TemporaryDirectory(prefix="content_frames_") as temp_directory:
            frames = await self._extract_scaled_frames(
                video_path, Path(temp_directory), timestamps, cell_w, cell_h, heavy
            )
            metadata = self._metadata_lines(video_path, probe) if self.show_metadata else None
            stamps = [item[1] for item in frames] if self.show_timestamp else None
            await asyncio.to_thread(
                self.merge_frames_to_grid,
                [item[0] for item in frames],
                output_path,
                stamps,
                metadata,
                (cell_w, cell_h),
            )
            if frames:
                await self._write_thumb_from_frame(video_path, frames[0][0])
        logger.info("网格封面完成：%s (%s) -> %s", video_path.name, self.grid_layout, output_path)
        return output_path

    async def build_content_page_async(self, video_path: Path, output_path: Path | None = None) -> Path:
        video_path = Path(video_path)
        if not video_path.is_file():
            raise FileNotFoundError(f"视频不存在：{video_path}")
        output_path = resolve_page_output(video_path, "_content.jpg", output_path, self.page_dir)

        try:
            return await self._build_fast(video_path, output_path)
        except NoVideoStreamError:
            raise
        except Exception as err:
            logger.warning("批量抽帧失败，回退逐帧: %s", err)
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
