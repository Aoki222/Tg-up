"""目录说明模板：继承、占位符，以及封面模式沿路由往上找。"""

from datetime import datetime
from pathlib import Path

from src.domain.caption import build_ingest_caption, render_caption, resolve_caption_template, resolve_preview
from src.domain.task import AfterSuccess
from src.domain.upload_settings import FolderRoute, PreviewMode, UploadSettings
from src.pipeline.ingest.policy import IngestPolicy


def _settings(tmp_path: Path, **kwargs) -> UploadSettings:
    base = dict(
        chat_id=-100,
        observer_paths=(tmp_path,),
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
        caption_template="{stem}",
    )
    base.update(kwargs)
    return UploadSettings(**base)


def test_placeholders_escapes_and_unknown_stay(tmp_path: Path) -> None:
    folder = tmp_path / "movies"
    folder.mkdir()
    path = folder / "Film.mp4"
    path.write_bytes(b"1234")
    route = FolderRoute(path=tmp_path, chat_id=-100, name="片库", dest_id="-100")
    text = render_caption(
        "{{ok}} {file_name} {stem} {ext} {folder} {rel_path} {route} {size} {date} {nope}",
        path,
        size=path.stat().st_size,
        mtime=path.stat().st_mtime,
        route=route,
    )
    assert text.startswith("{ok} Film.mp4 Film mp4 movies movies/Film.mp4 片库 4 B ")
    assert "{nope}" in text
    assert datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d") in text
    assert len(render_caption("字" * 2000, path, size=1, mtime=None, route=route)) == 1024


def test_caption_and_preview_walk_up_until_a_route_sets_them(tmp_path: Path) -> None:
    root = tmp_path / "download"
    child = root / "anime"
    child.mkdir(parents=True)
    video = child / "a.mp4"
    video.write_bytes(b"x")
    settings = _settings(
        tmp_path,
        caption_template="全局 {stem}",
        preview=PreviewMode.GRID,
        routes=(
            FolderRoute(
                path=root,
                chat_id=-100,
                name="下载",
                dest_id="-100",
                caption_template="{folder}/{stem}",
                preview=PreviewMode.FIRST_FRAME,
            ),
            FolderRoute(path=child, chat_id=-200, name="动漫", dest_id="-200"),
        ),
    )
    assert resolve_caption_template(video, settings) == "{folder}/{stem}"
    assert resolve_preview(video, settings) is PreviewMode.FIRST_FRAME
    assert build_ingest_caption(video, settings, 1) == "anime/a"

    explicit = _settings(
        tmp_path,
        routes=(
            FolderRoute(
                path=root,
                chat_id=-100,
                dest_id="-100",
                caption_template="{stem}",
                preview=PreviewMode.GRID,
            ),
            FolderRoute(
                path=child,
                chat_id=-200,
                dest_id="-200",
                caption_template="",
                preview=PreviewMode.OFF,
            ),
        ),
    )
    assert resolve_caption_template(video, explicit) == ""
    assert build_ingest_caption(video, explicit, 1) == ""
    decision = IngestPolicy().decide(video, explicit)
    assert decision.need_single is False
    assert decision.need_content is False
    assert decision.chat_id == -200
