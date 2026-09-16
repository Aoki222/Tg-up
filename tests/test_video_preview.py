from pathlib import Path

from src.utils.video_preview import (
    GridPreview,
    grid_seek_timestamps,
    interval_seek_timestamps,
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
