import math
from pathlib import Path

from PIL import Image

from src.utils.video_preview import (
    GRID_SEEK_BATCH,
    GridPreview,
    VideoProbe,
    cell_pixel_size,
    grid_seek_timestamps,
    interval_seek_timestamps,
    preview_decode_is_heavy,
)


def test_grid_timestamps_are_segment_midpoints() -> None:
    stamps = grid_seek_timestamps(3600.0, 15)
    assert len(stamps) == 15
    assert stamps[0] == 120.0
    assert stamps[1] == 360.0
    assert stamps[-1] == 3480.0
    assert all(0 < t < 3600 for t in stamps)


def test_short_video_timestamps_stay_inside_duration() -> None:
    stamps = grid_seek_timestamps(5.0, 15)
    assert len(stamps) == 15
    assert stamps[0] == 5.0 * 0.5 / 15
    assert all(0 < t < 5 for t in stamps)


def test_invalid_duration_or_count_is_empty() -> None:
    assert grid_seek_timestamps(0, 15) == []
    assert grid_seek_timestamps(10, 0) == []


def test_interval_timestamps_start_at_half_second() -> None:
    stamps = interval_seek_timestamps(60, 15)
    assert stamps[0] == 0.5
    assert stamps[1] == 60.5
    assert len(stamps) == 15


async def test_extract_uses_ss_before_input(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    seen: list[list[str]] = []

    async def fake_ffmpeg(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
        seen.append(list(arguments))
        output = Path(arguments[-1])
        output.write_bytes(b"jpg")

    async def fake_duration(self, video_path: Path) -> float:
        return 3600.0

    monkeypatch.setattr("src.utils.video_preview.run_ffmpeg_command", fake_ffmpeg)
    monkeypatch.setattr(GridPreview, "get_video_duration", fake_duration)

    frames = await GridPreview().extract_interval_frames(video, tmp_path / "frames")
    assert len(frames) == 15
    assert len(seen) == 15
    for arguments in seen:
        joined = " ".join(arguments)
        assert "fps=" not in joined
        ss_at = arguments.index("-ss")
        i_at = arguments.index("-i")
        assert ss_at < i_at
        assert arguments[arguments.index("-frames:v") + 1] == "1"


async def test_missing_duration_still_seeks_not_fps_filter(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    seen: list[list[str]] = []

    async def fake_ffmpeg(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
        seen.append(list(arguments))
        Path(arguments[-1]).write_bytes(b"jpg")

    async def no_duration(self, video_path: Path) -> float | None:
        return None

    monkeypatch.setattr("src.utils.video_preview.run_ffmpeg_command", fake_ffmpeg)
    monkeypatch.setattr(GridPreview, "get_video_duration", no_duration)

    frames = await GridPreview().extract_interval_frames(video, tmp_path / "frames")
    assert len(frames) == 15
    assert all("-ss" in arguments and arguments.index("-ss") < arguments.index("-i") for arguments in seen)
    assert all("fps=" not in " ".join(arguments) for arguments in seen)


def test_preview_decode_is_heavy_for_vp9_av1_and_above_1080p() -> None:
    assert preview_decode_is_heavy(VideoProbe(1, 3840, 2160, None, "vp9"))
    assert preview_decode_is_heavy(VideoProbe(1, 1920, 1080, None, "av1"))
    assert preview_decode_is_heavy(VideoProbe(1, 3840, 2160, None, "h264"))
    assert not preview_decode_is_heavy(VideoProbe(1, 1920, 1080, None, "h264"))
    assert not preview_decode_is_heavy(VideoProbe(1, 1280, 720, None, ""))


def test_cell_pixel_size_keeps_aspect_and_stays_even() -> None:
    wide_w, wide_h = cell_pixel_size(1200, 3, 1920, 1080)
    tall_w, tall_h = cell_pixel_size(1200, 3, 1080, 1920)
    assert wide_w % 2 == 0 and wide_h % 2 == 0
    assert tall_w % 2 == 0 and tall_h % 2 == 0
    assert abs(wide_w / wide_h - 16 / 9) < 0.03
    assert tall_h > tall_w
    assert abs(tall_w / tall_h - 9 / 16) < 0.03


def _ss_before_each_input(arguments: list[str]) -> bool:
    seek_at = [index for index, item in enumerate(arguments) if item == "-ss"]
    input_at = [index for index, item in enumerate(arguments) if item == "-i"]
    return len(seek_at) == len(input_at) and all(seek < src for seek, src in zip(seek_at, input_at))


async def test_grid_cover_seeks_in_batches(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    seen: list[list[str]] = []

    async def fake_probe(self, video_path: Path) -> VideoProbe:
        return VideoProbe(3600.0, 1920, 1080, 4096)

    async def fake_ffmpeg(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
        seen.append(list(arguments))
        for item in arguments:
            if str(item).endswith(".jpg"):
                Image.new("RGB", (8, 8), "blue").save(item, "JPEG")

    async def no_subprocess(*args, **kwargs):
        raise AssertionError(args[:3])

    monkeypatch.setattr(GridPreview, "probe_video", fake_probe)
    monkeypatch.setattr("src.utils.video_preview.run_ffmpeg_command", fake_ffmpeg)
    monkeypatch.setattr("src.utils.video_preview.asyncio.create_subprocess_exec", no_subprocess)

    preview = GridPreview(page_dir=tmp_path)
    page = await preview.build_content_page_async(video, None)
    assert page.is_file()
    assert len(seen) == math.ceil(15 / GRID_SEEK_BATCH)
    assert sum(arguments.count("-ss") for arguments in seen) == 15
    for arguments in seen:
        joined = " ".join(arguments)
        assert "fps=" not in joined
        assert _ss_before_each_input(arguments)
        assert "-threads" in arguments
        assert any(item.startswith("scale=") for item in arguments)
    with Image.open(page) as sheet:
        assert 1000 <= sheet.size[0] <= 1200
        assert sheet.size[1] > 900
    thumb = tmp_path / "clip_thumb.jpg"
    assert thumb.is_file()
    with Image.open(thumb) as thumb_image:
        assert max(thumb_image.size) <= 320


async def test_heavy_webm_grabs_one_keyframe_at_a_time(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.webm"
    video.write_bytes(b"fake")
    seen: list[list[str]] = []

    async def fake_probe(self, video_path: Path) -> VideoProbe:
        return VideoProbe(600.0, 3840, 2160, 8000, "vp9")

    async def fake_ffmpeg(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
        seen.append(list(arguments))
        for item in arguments:
            if str(item).endswith(".jpg"):
                Image.new("RGB", (8, 8), "red").save(item, "JPEG")

    monkeypatch.setattr(GridPreview, "probe_video", fake_probe)
    monkeypatch.setattr("src.utils.video_preview.run_ffmpeg_command", fake_ffmpeg)

    page = await GridPreview(grid_layout="3x3", show_metadata=False, page_dir=tmp_path).build_content_page_async(
        video, None
    )
    assert page.is_file()
    assert len(seen) == 9
    for arguments in seen:
        joined = " ".join(arguments)
        assert arguments.count("-i") == 1
        assert "fps=" not in joined
        assert arguments.index("-noaccurate_seek") < arguments.index("-ss") < arguments.index("-i")
        assert "-threads" in arguments


async def test_batch_failure_falls_back_to_one_seek_per_frame(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    seen: list[list[str]] = []

    async def fake_probe(self, video_path: Path) -> VideoProbe:
        return VideoProbe(3600.0, 1920, 1080, None)

    async def fail_batch(self, *args, **kwargs):
        raise RuntimeError("batch failed")

    async def fake_duration(self, video_path: Path) -> float:
        return 3600.0

    async def fake_ffmpeg(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
        seen.append(list(arguments))
        Path(arguments[-1]).write_bytes(b"jpg-not-really")

    monkeypatch.setattr(GridPreview, "probe_video", fake_probe)
    monkeypatch.setattr(GridPreview, "_extract_scaled_frames", fail_batch)
    monkeypatch.setattr(GridPreview, "get_video_duration", fake_duration)
    monkeypatch.setattr("src.utils.video_preview.run_ffmpeg_command", fake_ffmpeg)
    def fake_merge(self, frame_paths, output_path, *args, **kwargs):
        path = Path(output_path)
        path.write_bytes(b"ok")
        return path

    monkeypatch.setattr(GridPreview, "merge_frames_to_grid", fake_merge)

    page = await GridPreview(page_dir=tmp_path).build_content_page_async(video, None)
    assert page.is_file()
    assert len(seen) == 15
    assert all(_ss_before_each_input(arguments) for arguments in seen)
    assert all("fps=" not in " ".join(arguments) for arguments in seen)


async def test_video_thumbnail_from_image(tmp_path: Path) -> None:
    from PIL import Image
    from src.utils.video_preview import VideoThumbnail

    # 创建一个 1920x1080 的单帧图
    src_img = tmp_path / "movie_single.jpg"
    img = Image.new("RGB", (1920, 1080), color="blue")
    img.save(src_img, "JPEG")

    thumb = VideoThumbnail(page_dir=tmp_path)
    res = await thumb.ensure_thumbnail_async(tmp_path / "movie.mp4", existing_page_path=src_img)
    assert res is not None
    assert res.is_file()
    assert res.name == "movie_thumb.jpg"

    with Image.open(res) as out_img:
        w, h = out_img.size
        assert max(w, h) <= 320
        # 1920x1080 缩放到 320 宽，高度应为 180
        assert w == 320
        assert h == 180


async def test_video_thumbnail_extract_from_video(tmp_path: Path, monkeypatch) -> None:
    from src.utils.video_preview import VideoThumbnail

    video = tmp_path / "sample.mp4"
    video.write_bytes(b"dummy")

    seen_args = []
    async def fake_ffmpeg(arguments: list[str], ffmpeg_path: str = "ffmpeg") -> None:
        seen_args.append(arguments)
        Path(arguments[-1]).write_bytes(b"jpeg-data")

    monkeypatch.setattr("src.utils.video_preview.run_ffmpeg_command", fake_ffmpeg)

    thumb = VideoThumbnail(page_dir=tmp_path)
    res = await thumb.ensure_thumbnail_async(video)
    assert res is not None
    assert res.is_file()
    assert len(seen_args) == 1
    assert "-vf" in seen_args[0]
    assert any("force_original_aspect_ratio=decrease" in a for a in seen_args[0])
    assert any("scale=w=320:h=320" in a for a in seen_args[0])
    


async def test_video_thumbnail_from_vertical_image(tmp_path: Path) -> None:
    from PIL import Image
    from src.utils.video_preview import VideoThumbnail

    # 创建一个 1080x1920 的竖屏单帧图
    src_img = tmp_path / "vertical_single.jpg"
    img = Image.new("RGB", (1080, 1920), color="red")
    img.save(src_img, "JPEG")

    thumb = VideoThumbnail(page_dir=tmp_path)
    res = await thumb.ensure_thumbnail_async(tmp_path / "vertical.mp4", existing_page_path=src_img)
    assert res is not None
    assert res.is_file()

    with Image.open(res) as out_img:
        w, h = out_img.size
        # 宽和高必须均 <= 320
        assert max(w, h) <= 320
        assert w == 180
        assert h == 320
