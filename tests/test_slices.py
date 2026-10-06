"""切片段数、说明和卡片阶段。不跑 ffmpeg。"""

from src.domain.limits import TELEGRAM_BOT_MAX_BYTES
from src.domain.slices import (
    MSG_CUTTING,
    MSG_INTERRUPTED,
    MSG_NO_DISK,
    MSG_TOO_DENSE,
    MSG_WAIT,
    SLICE_MAX_PARTS,
    SLICE_TARGET_BYTES,
    is_sliceable_video,
    minimum_slice_parts,
    msg_need_parts,
    msg_part,
    msg_part_failed,
    msg_released,
    part_caption,
    parts_to_fit,
    segment_filename,
    slice_phase,
    user_dispatch_blocked,
)
from pathlib import Path


def test_minimum_parts_stays_under_the_bot_limit() -> None:
    assert minimum_slice_parts(TELEGRAM_BOT_MAX_BYTES + 1) == 2
    assert minimum_slice_parts(SLICE_TARGET_BYTES * 3) == 3
    assert minimum_slice_parts(SLICE_TARGET_BYTES * SLICE_MAX_PARTS + 1) == SLICE_MAX_PARTS + 1


def test_oversized_part_asks_for_more_segments() -> None:
    assert parts_to_fit(2, SLICE_TARGET_BYTES) == 2
    assert parts_to_fit(2, TELEGRAM_BOT_MAX_BYTES + 1) >= 3


def test_names_caption_and_which_files_can_be_sliced() -> None:
    assert segment_filename(Path("电影.mkv"), 2, 6, ".mp4") == "电影.part02-of-06.mp4"
    assert part_caption("原说明", 2, 6) == "原说明\n（2/6）"
    assert part_caption("", 1, 2) == "（1/2）"
    long = part_caption("字" * 2000, 1, 2)
    assert len(long) == 1024
    assert long.endswith("（1/2）")
    assert is_sliceable_video("a.MP4")
    assert not is_sliceable_video("archive.zip")


def test_phase_controls_personal_upload() -> None:
    assert slice_phase("超过 2GB，不自动分配给 Bot") == "idle"
    assert slice_phase(MSG_WAIT) == "queued"
    assert slice_phase(MSG_CUTTING) == "cutting"
    assert slice_phase(msg_released(6)) == "released"
    assert slice_phase(msg_part(2, 6)) == "uploading"
    assert slice_phase(msg_part_failed(3, 6)) == "failed"
    assert slice_phase(msg_need_parts(4)) == "blocked"
    assert slice_phase(MSG_INTERRUPTED) == "blocked"
    assert slice_phase(MSG_NO_DISK) == "blocked"
    assert slice_phase(MSG_TOO_DENSE) == "blocked"
    assert user_dispatch_blocked(msg_released(6))
    assert user_dispatch_blocked(MSG_WAIT)
    assert user_dispatch_blocked(msg_part_failed(1, 2))
    assert not user_dispatch_blocked(MSG_INTERRUPTED)
    assert not user_dispatch_blocked("超过 2GB，不自动分配给 Bot")
