from pathlib import Path

from src.domain.task import AfterSuccess
from src.domain.upload_settings import PreviewMode, UploadSettings
from src.pipeline.ingest.policy import IngestPolicy


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
        watch_extensions=frozenset({".mp4"}),
    )
    base.update(kwargs)
    return UploadSettings(**base)


def test_watch_list_rejects_other_suffix() -> None:
    decision = IngestPolicy().decide(Path("a.pdf"), _settings())
    assert decision.allowed is False


def test_empty_watch_extensions_allows_any_file() -> None:
    decision = IngestPolicy().decide(
        Path("notes.pdf"),
        _settings(watch_extensions=frozenset()),
    )
    assert decision.allowed is True
    assert decision.need_single is False


def test_video_gets_preview_when_enabled() -> None:
    decision = IngestPolicy().decide(Path("clip.mp4"), _settings())
    assert decision.allowed is True
    assert decision.need_single is True
    assert decision.need_content is False
