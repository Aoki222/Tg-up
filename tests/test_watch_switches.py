from pathlib import Path
import pytest
from src.domain.settings_hub import load_upload_settings, render_upload_toml, SettingsHub
from src.domain.upload_settings import FolderRoute, ObserverRoot, UploadSettings, PreviewMode, AfterSuccess
from src.pipeline.ingest.policy import IngestPolicy


def _make_settings(tmp_path: Path, **kwargs) -> UploadSettings:
    defaults = dict(
        chat_id=-100,
        observer_paths=(tmp_path / "download",),
        observer_roots=(ObserverRoot(path=tmp_path / "download", enabled=True),),
        page_dir=tmp_path / "page",
        archive_dir=tmp_path / "uploaded",
        preview=PreviewMode.OFF,
        topic_creation_enabled=True,
        after_success=AfterSuccess.KEEP,
        concurrency=1,
        max_retries=3,
        upload_timeout_seconds=1200,
        assigned_timeout_seconds=600,
        stable_timeout_seconds=1800,
        watch_extensions=frozenset({".mp4"}),
        routes=(),
        chats=(),
        drive_folders=(),
        auto_slice=False,
        caption_template="",
        watch_enabled=True,
    )
    defaults.update(kwargs)
    return UploadSettings(**defaults)


def test_roundtrip_observer_roots_and_watch_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    root1.mkdir()
    root2.mkdir()
    text = render_upload_toml({
        "chat_id": -100,
        "watch_enabled": False,
        "observer_roots": [
            {"path": str(root1), "enabled": True},
            {"path": str(root2), "enabled": False},
        ],
    })
    config = tmp_path / "upload.toml"
    config.write_text(text, encoding="utf-8")
    settings = load_upload_settings(config, tmp_path)
    assert settings.watch_enabled is False
    assert len(settings.observer_roots) == 2
    r_map = {r.path.resolve(): r.enabled for r in settings.observer_roots}
    assert r_map[root1.resolve()] is True
    assert r_map[root2.resolve()] is False


def test_subroute_disabled_prevents_leaking_to_parent(tmp_path: Path) -> None:
    """测试防穿透：父目录开启，子目录单独禁用，子目录文件不落入父目录。"""
    parent = tmp_path / "download"
    child = parent / "private"
    child.mkdir(parents=True)
    video = child / "secret.mp4"
    video.write_bytes(b"data")

    settings = _make_settings(
        tmp_path,
        routes=(
            FolderRoute(path=parent.resolve(), chat_id=-100111, name="parent_public", enabled=True),
            FolderRoute(path=child.resolve(), chat_id=-100222, name="child_private", enabled=False),
        )
    )

    policy = IngestPolicy()
    decision = policy.decide(video, settings)
    # 子目录规则禁用，必须就地阻断，不允许穿透到 parent_public
    assert decision.allowed is False
    assert decision.is_disabled is True
    assert decision.matched is False


def test_subroute_enabled_overrides_parent(tmp_path: Path) -> None:
    """测试正常覆盖：父目录开启，子目录也开启，子目录文件正确命中子目录。"""
    parent = tmp_path / "download"
    child = parent / "movies"
    child.mkdir(parents=True)
    video = child / "film.mp4"
    video.write_bytes(b"data")

    settings = _make_settings(
        tmp_path,
        routes=(
            FolderRoute(path=parent.resolve(), chat_id=-100111, name="parent_public", enabled=True),
            FolderRoute(path=child.resolve(), chat_id=-100222, name="child_movies", enabled=True),
        )
    )

    policy = IngestPolicy()
    decision = policy.decide(video, settings)
    assert decision.allowed is True
    assert decision.chat_id == -100222


async def test_api_toggle_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from src.api.app import create_api
    from src.adapters.progress import ProgressHub
    from src.api.routes.settings import TogglePathPayload

    root_dir = tmp_path / "root1"
    sub_dir = root_dir / "sub"
    sub_dir.mkdir(parents=True)

    text = render_upload_toml({
        "chat_id": -100,
        "watch_enabled": True,
        "observer_roots": [
            {"path": str(root_dir), "enabled": True},
        ],
        "routes": [
            {
                "name": "sub",
                "path": str(sub_dir),
                "chat_id": -100222,
                "enabled": True,
            }
        ],
    })
    cfg_file = tmp_path / "upload.toml"
    cfg_file.write_text(text, encoding="utf-8")
    hub = SettingsHub(cfg_file, tmp_path)

    app = create_api(
        progress_hub=ProgressHub(),
        settings_hub=hub,
    )

    def find_endpoint(path: str, method: str):
        for route in app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route.endpoint
        raise KeyError((path, method))

    # 1. 测试 GET /api/settings
    settings_data = await find_endpoint("/api/settings", "GET")()
    assert settings_data["watch_enabled"] is True
    assert settings_data["observer_roots"][0]["enabled"] is True

    # 2. 测试 POST /api/settings/watch/toggle
    toggle_watch_data = await find_endpoint("/api/settings/watch/toggle", "POST")()
    assert toggle_watch_data["watch_enabled"] is False
    assert hub.get().watch_enabled is False

    # 3. 测试 POST /api/settings/roots/toggle
    toggle_root_data = await find_endpoint("/api/settings/roots/toggle", "POST")(TogglePathPayload(path=str(root_dir)))
    assert toggle_root_data["path"] == str(root_dir)
    assert hub.get().observer_roots[0].enabled is False

    # 4. 测试 POST /api/settings/routes/toggle
    toggle_route_data = await find_endpoint("/api/settings/routes/toggle", "POST")(TogglePathPayload(path=str(sub_dir)))
    assert toggle_route_data["path"] == str(sub_dir)
    assert hub.get().routes[0].enabled is False


