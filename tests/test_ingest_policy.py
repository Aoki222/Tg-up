from pathlib import Path

from src.domain.task import AfterSuccess
from src.domain.upload_settings import FolderRoute, PreviewMode, UploadSettings
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


def test_no_routes_falls_back_to_global() -> None:
    decision = IngestPolicy().decide(Path("clip.mp4"), _settings(chat_id=-100, topic_creation_enabled=True))
    assert decision.chat_id == -100
    assert decision.topic_enabled is True


def test_folder_routes_longest_prefix_and_sibling_fallback(tmp_path: Path) -> None:
    movies = tmp_path / "download" / "movies"
    action = movies / "action"
    music = tmp_path / "download" / "music"
    action.mkdir(parents=True)
    music.mkdir(parents=True)
    nested = action / "a.mp4"
    nested.write_bytes(b"x")
    in_movies = movies / "b.mp4"
    in_movies.write_bytes(b"x")
    sibling = music / "c.mp4"
    sibling.write_bytes(b"x")

    settings = _settings(
        chat_id=-100,
        topic_creation_enabled=True,
        routes=(
            FolderRoute(path=movies.resolve(), chat_id=-100111, name="movies"),
            FolderRoute(path=action.resolve(), chat_id=-100222, name="action", topic_enabled=False),
        ),
    )
    policy = IngestPolicy()
    deep = policy.decide(nested, settings)
    assert deep.chat_id == -100222
    assert deep.topic_enabled is False
    mid = policy.decide(in_movies, settings)
    assert mid.chat_id == -100111
    assert mid.topic_enabled is True
    other = policy.decide(sibling, settings)
    assert other.chat_id == -100
    assert other.topic_enabled is True


def test_disabled_route_falls_back_to_global(tmp_path: Path) -> None:
    folder = tmp_path / "movies"
    folder.mkdir()
    clip = folder / "a.mp4"
    clip.write_bytes(b"x")
    settings = _settings(
        chat_id=-100,
        topic_creation_enabled=True,
        routes=(
            FolderRoute(
                path=folder.resolve(),
                chat_id=-100111,
                name="movies",
                enabled=False,
            ),
        ),
    )
    decision = IngestPolicy().decide(clip, settings)
    assert decision.chat_id == -100
    assert decision.topic_enabled is True


def test_route_topic_none_follows_global(tmp_path: Path) -> None:
    folder = tmp_path / "anime"
    folder.mkdir()
    clip = folder / "a.mp4"
    clip.write_bytes(b"x")
    settings = _settings(
        chat_id=-100,
        topic_creation_enabled=False,
        routes=(FolderRoute(path=folder.resolve(), chat_id=-100333, name="anime", topic_enabled=None),),
    )
    decision = IngestPolicy().decide(clip, settings)
    assert decision.chat_id == -100333
    assert decision.topic_enabled is False
