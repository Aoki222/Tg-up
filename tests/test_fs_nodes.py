from pathlib import Path
import pytest
from fastapi import HTTPException

from src.api.app import create_api
from src.adapters.progress import ProgressHub
from src.domain.settings_hub import SettingsHub
from src.domain.upload_settings import FolderRoute, PreviewMode, UploadSettings
from src.domain.task import AfterSuccess
from src.pipeline.ingest.policy import inspect_path_route


def _settings(tmp_path: Path, **kwargs) -> UploadSettings:
    base = dict(
        chat_id=-100,
        observer_paths=(tmp_path / "download",),
        page_dir=tmp_path / "page",
        archive_dir=tmp_path / "uploaded",
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


def test_inspect_path_route_inheritance(tmp_path: Path) -> None:
    download = tmp_path / "download"
    movies = download / "movies"
    anime = movies / "anime"
    film = anime / "film.mp4"
    movies.mkdir(parents=True)
    anime.mkdir(parents=True)

    settings = _settings(
        tmp_path,
        routes=(
            FolderRoute(path=download.resolve(), chat_id=-100111, dest_id="-100111", name="根目录"),
            FolderRoute(path=anime.resolve(), chat_id=-100222, dest_id="-100222", name="动漫专属"),
        ),
    )

    # 1. 根目录：自身是专属规则
    root_info = inspect_path_route(download, settings)
    assert root_info["matched"] is True
    assert root_info["is_explicit"] is True
    assert root_info["dest_id"] == "-100111"

    # 2. movies：继承 download
    movies_info = inspect_path_route(movies, settings)
    assert movies_info["matched"] is True
    assert movies_info["is_explicit"] is False
    assert movies_info["dest_id"] == "-100111"
    assert movies_info["inherited_from"] == str(download.resolve())

    # 3. anime：自身是专属规则
    anime_info = inspect_path_route(anime, settings)
    assert anime_info["matched"] is True
    assert anime_info["is_explicit"] is True
    assert anime_info["dest_id"] == "-100222"

    # 4. film.mp4：继承自 anime 专属规则
    film_info = inspect_path_route(film, settings)
    assert film_info["matched"] is True
    assert film_info["is_explicit"] is False
    assert film_info["dest_id"] == "-100222"


@pytest.mark.asyncio
async def test_fs_nodes_api_roots_and_children(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    download = tmp_path / "download"
    movies = download / "movies"
    movies.mkdir(parents=True)

    video1 = movies / "a.mp4"
    video1.write_bytes(b"hello world")
    note = movies / "readme.txt"
    note.write_bytes(b"some note")

    config = tmp_path / "upload.toml"
    config.write_text(
        f'chat_id = -100\nobserver_paths = ["{download.as_posix()}"]\npreview = "off"\n'
        f'[[routes]]\npath = "{download.as_posix()}"\nchat_id = -100111\nplatform = "telegram"\n',
        encoding="utf-8",
    )
    hub = SettingsHub(config, tmp_path)
    app = create_api(progress_hub=ProgressHub(), settings_hub=hub)

    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/fs/nodes")

    # 1. 不传 path 返回根目录列表
    data = await endpoint(path=None)
    assert len(data["items"]) == 1
    root_node = data["items"][0]
    assert root_node["is_root"] is True
    assert root_node["is_dir"] is True
    assert root_node["current_route"]["matched"] is True

    # 2. 传 download 返回子目录 movies
    data = await endpoint(path=download.as_posix())
    children = data["items"]
    assert len(children) == 1
    assert children[0]["name"] == "movies"
    assert children[0]["is_dir"] is True

    # 3. 传 movies 返回 a.mp4 和 readme.txt
    data = await endpoint(path=movies.as_posix())
    file_nodes = data["items"]
    assert len(file_nodes) == 2
    names = {item["name"]: item for item in file_nodes}
    assert "a.mp4" in names
    assert names["a.mp4"]["is_dir"] is False
    assert names["a.mp4"]["supported_ext"] is True
    assert names["a.mp4"]["size"] == len(b"hello world")
    assert "readme.txt" in names
    assert names["readme.txt"]["supported_ext"] is False


@pytest.mark.asyncio
async def test_fs_nodes_api_path_traversal_forbidden(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    download = tmp_path / "download"
    download.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()

    config = tmp_path / "upload.toml"
    config.write_text(
        f'chat_id = -100\nobserver_paths = ["{download.as_posix()}"]\npreview = "off"\n',
        encoding="utf-8",
    )
    hub = SettingsHub(config, tmp_path)
    app = create_api(progress_hub=ProgressHub(), settings_hub=hub)

    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/fs/nodes")

    # 尝试访问非映射目录应当引发 403 Forbidden
    with pytest.raises(HTTPException) as exc1:
        await endpoint(path=secret.as_posix())
    assert exc1.value.status_code == 403

    # 相对路径穿越访问父级应当引发 403 Forbidden
    with pytest.raises(HTTPException) as exc2:
        await endpoint(path=(download / ".." / "secret").as_posix())
    assert exc2.value.status_code == 403
