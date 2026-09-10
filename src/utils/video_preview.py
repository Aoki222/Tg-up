"""视频封面图生成：单帧截图与间隔截图拼图，只落文件，写库后续再接。"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from ..config import PAGE_DIR
from ..logger import get_logger

logger = get_logger(__name__)


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


def run_ffmpeg_command(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
    """同步跑一条 ffmpeg 命令，失败抛错并记日志。"""
    command = [ffmpeg_path, "-y", *arguments]
    logger.info("执行 ffmpeg：%s", " ".join(command))
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        logger.error("ffmpeg 失败：%s", completed.stderr[-2000:])
        raise RuntimeError(f"ffmpeg 失败：{completed.stderr[-500:]}")


class FirstFramePreview:
    """职责：截视频第 1 秒画面存为单图，返回落盘路径（page_path 待写库）。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg", page_dir: Path | None = None):
        self.ffmpeg_path = ffmpeg_path
        self.page_dir = page_dir

    def extract_first_frame(self, video_path: Path, output_path: Path | None = None) -> Path:
        # 1. 校验输入，输出默认落 page 目录
        video_path = Path(video_path)
        output_path = resolve_page_output(video_path, "_single.jpg", output_path, self.page_dir)
        if not video_path.is_file():
            raise FileNotFoundError(f"视频不存在：{video_path}")
        # 2. 取第 1 秒单帧（-ss 在 -i 前为快速定位）
        run_ffmpeg_command(
            ["-ss", "1", "-i", str(video_path), "-vframes", "1", str(output_path)],
            self.ffmpeg_path,
        )
        # 3. 校验产物
        if not output_path.is_file():
            raise RuntimeError(f"截图未生成：{output_path}")
        logger.info("单帧截图完成：%s -> %s", video_path, output_path)
        return output_path

    async def extract_first_frame_async(self, video_path: Path, output_path: Path | None = None) -> Path:
        # 异步壳：subprocess 是阻塞的，丢线程池避免卡主循环
        return await asyncio.to_thread(self.extract_first_frame, video_path, output_path)


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
        # 网格总数：3x5=15 格，不足补黑格
        return self.GRID_COLUMNS * self.GRID_ROWS

    def get_video_duration(self, video_path: Path) -> float | None:
        # 读视频总时长，失败回 None 走 60 秒间隔兜底
        try:
            completed = subprocess.run(
                [self.ffprobe_path, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                capture_output=True, text=True,
            )
            return float(completed.stdout.strip())
        except Exception:
            logger.warning("ffprobe 读时长失败，走间隔抽帧：%s", video_path)
            return None

    def extract_interval_frames(self, video_path: Path, frame_directory: Path) -> list[Path]:
        # 1. 校验输入，建帧目录
        video_path = Path(video_path)
        frame_directory = Path(frame_directory)
        if not video_path.is_file():
            raise FileNotFoundError(f"视频不存在：{video_path}")
        frame_directory.mkdir(parents=True, exist_ok=True)
        # 2. 优先按总时长均分 15 帧，时长未知才按 60 秒间隔抽再匀选 15 张
        duration = self.get_video_duration(video_path)
        if duration and duration > 0:
            run_ffmpeg_command(
                ["-i", str(video_path), "-vf", f"fps={self.frame_count}/{duration}",
                 str(frame_directory / "frame_%04d.jpg")],
                self.ffmpeg_path,
            )
        else:
            run_ffmpeg_command(
                ["-i", str(video_path), "-vf", f"fps=1/{self.interval_seconds}",
                 str(frame_directory / "frame_%04d.jpg")],
                self.ffmpeg_path,
            )
        # 3. 按序号收帧并匀选到固定 15 张，不足补首帧，仍不足后补黑格由拼图处理
        frame_paths = sorted(frame_directory.glob("frame_*.jpg"))
        if not frame_paths:
            fallback = frame_directory / "frame_0001.jpg"
            run_ffmpeg_command(
                ["-ss", "1", "-i", str(video_path), "-vframes", "1", str(fallback)],
                self.ffmpeg_path,
            )
            frame_paths = [fallback] if fallback.is_file() else []
        if not frame_paths:
            raise RuntimeError(f"未抽到任何帧：{video_path}")
        if len(frame_paths) > self.frame_count:
            step = len(frame_paths) / self.frame_count
            frame_paths = [frame_paths[int(index * step)] for index in range(self.frame_count)]
        logger.info("抽帧完成：%s 共 %s 张", video_path, len(frame_paths))
        return frame_paths

    def merge_frames_to_grid(self, frame_paths: list[Path], output_path: Path) -> Path:
        # 固定 3 列 x 5 行网格：小格 320x180 居中裁剪，不足 15 张补黑格，小体积 JPEG 落盘
        from PIL import ImageOps

        if not frame_paths:
            raise ValueError("帧列表为空，无法拼图")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # 1. 取最多 15 张逐格裁成统一小格
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
        # 2. 低质量高压缩：quality=70 + optimize，960x900 网格约 200-400KB
        grid_image.save(output_path, "JPEG", quality=self.jpeg_quality, optimize=True)
        logger.info("拼图完成：3x5 -> %s", output_path)
        return output_path

    def build_content_page(self, video_path: Path, output_path: Path | None = None) -> Path:
        # 1. 抽帧到临时目录，避免污染输出目录；2. 拼网格图默认落 page 目录
        video_path = Path(video_path)
        output_path = resolve_page_output(video_path, "_content.jpg", output_path, self.page_dir)
        with tempfile.TemporaryDirectory(prefix="content_frames_") as temp_directory:
            frame_paths = self.extract_interval_frames(video_path, Path(temp_directory))
            # 2. 拼长图落盘
            return self.merge_frames_to_grid(frame_paths, output_path)

    async def build_content_page_async(self, video_path: Path, output_path: Path | None = None) -> Path:
        # 异步壳：抽帧+拼图全是阻塞操作，丢线程池
        return await asyncio.to_thread(self.build_content_page, video_path, output_path)
