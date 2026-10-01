from pathlib import Path

from src.pipeline.ingest.filters import check_transient_file, is_transient_file
from src.pipeline.ingest.policy import IngestPolicy
from src.domain.upload_settings import FolderRoute, PreviewMode, UploadSettings
from src.domain.task import AfterSuccess


def _settings(**kwargs) -> UploadSettings:
    base = dict(
        chat_id=-100,
        observer_paths=(Path("download"),),
        page_dir=Path("page"),
        archive_dir=Path("uploaded"),
        preview=PreviewMode.FIRST_FRAME,
        topic_creation_enabled=True,
        after_success=AfterSuccess.KEEP,
        concurrency=1,
        max_retries=3,
        upload_timeout_seconds=1200,
        assigned_timeout_seconds=600,
        stable_timeout_seconds=30,
        watch_extensions=frozenset({".mp4", ".webm", ".mkv"}),
    )
    base.update(kwargs)
    return UploadSettings(**base)


def test_youtube_dash_intermediate_files_detected() -> None:
    # 4K 纯视频轨 (如 f401)
    res = check_transient_file(Path("/downloads/ydl/逃离 皮裤.f401.mp4"))
    assert res.is_transient is True
    assert res.source_platform == "youtube"
    assert "f401" in res.reason

    # Opus 纯音频轨 (如 f251)
    res = check_transient_file(Path("/downloads/ydl/逃离 皮裤.f251.webm"))
    assert res.is_transient is True
    assert res.source_platform == "youtube"
    assert "f251" in res.reason

    # 其他 YouTube DASH 常见格式 (f137 1080p, f140 m4a)
    assert is_transient_file(Path("video.f137.mp4")) is True
    assert is_transient_file(Path("audio.f140.m4a")) is True


def test_youtube_temp_merge_files_detected() -> None:
    res = check_transient_file(Path("/downloads/ydl/觉醒 👄M字唇的模特.temp.webm"))
    assert res.is_transient is True
    assert res.source_platform == "youtube"
    assert "temp" in res.reason

    assert is_transient_file(Path("video.temp.mp4")) is True


def test_generic_and_downloader_transient_files_detected() -> None:
    # yt-dlp .ytdl
    res = check_transient_file(Path("video.mp4.ytdl"))
    assert res.is_transient is True

    # 通用断点后缀
    assert is_transient_file(Path("video.mp4.part")) is True
    assert is_transient_file(Path("video.crdownload")) is True
    assert is_transient_file(Path("download.aria2")) is True
    assert is_transient_file(Path("tempfile.tmp")) is True

    # 隐藏临时文件
    assert is_transient_file(Path(".metube/queue.json")) is True
    assert is_transient_file(Path(".queue.json.xk2pzmmv.tmp")) is True


def test_normal_files_not_flagged() -> None:
    # 正常视频绝对不能误伤
    assert is_transient_file(Path("逃离 皮裤  🪼（model 菁菁） 4K高清.webm")) is False
    assert is_transient_file(Path("觉醒 👄M字唇的模特.mp4")) is False
    assert is_transient_file(Path("summer_vibes.mkv")) is False

    # 包含 f 或 temp 但不是分片命名的正常视频
    assert is_transient_file(Path("f401_normal_name.mp4")) is False
    assert is_transient_file(Path("temp_holiday_trip.mp4")) is False
    assert is_transient_file(Path("my_favorite_film.mp4")) is False


def test_policy_decide_blocks_transient_files() -> None:
    policy = IngestPolicy()
    settings = _settings(routes=(FolderRoute(path=Path.cwd(), chat_id=-100, dest_id="-100"),))

    # 临时分轨应该被判定为 is_transient=True 且 allowed=False
    decision = policy.decide(Path("觉醒.f251.webm"), settings)
    assert decision.is_transient is True
    assert decision.allowed is False
    assert decision.matched is False

    # 合成后的最终正规视频应该正常放行并需要截图
    valid_decision = policy.decide(Path("觉醒.webm"), settings)
    assert valid_decision.is_transient is False
    assert valid_decision.allowed is True
    assert valid_decision.need_single is True
