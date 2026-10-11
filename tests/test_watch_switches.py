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

    # 2. 测试 POST /api/settings/watch/toggle（全局关闭，联动所有根路径关闭）
    toggle_watch_data = await find_endpoint("/api/settings/watch/toggle", "POST")()
    assert toggle_watch_data["watch_enabled"] is False
    assert hub.get().watch_enabled is False
    assert toggle_watch_data["observer_roots"][0]["enabled"] is False
    assert hub.get().observer_roots[0].enabled is False

    # 3. 测试 POST /api/settings/roots/toggle（根路径重新开启，联动唤醒全局开关）
    toggle_root_data = await find_endpoint("/api/settings/roots/toggle", "POST")(TogglePathPayload(path=str(root_dir)))
    assert toggle_root_data["path"] == str(root_dir)
    assert toggle_root_data["watch_enabled"] is True
    assert hub.get().observer_roots[0].enabled is True
    assert hub.get().watch_enabled is True

    # 4. 测试 POST /api/settings/routes/toggle
    toggle_route_data = await find_endpoint("/api/settings/routes/toggle", "POST")(TogglePathPayload(path=str(sub_dir)))
    assert toggle_route_data["path"] == str(sub_dir)
    assert hub.get().routes[0].enabled is False


def test_save_from_payload_adds_and_removes_observer_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg_file = tmp_path / "upload.toml"
    cfg_file.write_text('chat_id = -100\n[[observer_roots]]\npath = "r1"\nenabled = true\n', encoding="utf-8")
    (tmp_path / "r1").mkdir()
    (tmp_path / "r2").mkdir()

    hub = SettingsHub(cfg_file, tmp_path)
    assert len(hub.get().observer_roots) == 1

    # 1. 模拟前端添加新路径并保存
    saved = hub.save_from_payload({
        "chat_id": -100,
        "observer_roots": [
            {"path": str(tmp_path / "r1"), "enabled": True},
            {"path": str(tmp_path / "r2"), "enabled": True},
        ],
    })
    assert len(saved["observer_roots"]) == 2
    assert len(hub.get().observer_roots) == 2
    r_map = {r.path.resolve(): r.enabled for r in hub.get().observer_roots}
    assert (tmp_path / "r2").resolve() in r_map

    # 2. 模拟前端删除旧路径并保存
    saved_after_delete = hub.save_from_payload({
        "chat_id": -100,
        "observer_roots": [
            {"path": str(tmp_path / "r2"), "enabled": True},
        ],
    })
    assert len(saved_after_delete["observer_roots"]) == 1
    assert len(hub.get().observer_roots) == 1
    assert hub.get().observer_roots[0].path.resolve() == (tmp_path / "r2").resolve()


async def test_subfolder_quick_toggle_block_and_unblock(tmp_path: Path, monkeypatch) -> None:
    """测试子目录快捷就地阻断与解阻断（恢复继承）。"""
    monkeypatch.chdir(tmp_path)
    from src.api.app import create_api
    from src.adapters.progress import ProgressHub
    from src.api.routes.settings import TogglePathPayload
    from src.pipeline.ingest.policy import inspect_path_route

    root_dir = tmp_path / "download"
    sub_dir = root_dir / "anime"
    sub_dir.mkdir(parents=True)
    video = sub_dir / "ep1.mp4"
    video.write_bytes(b"video-bytes")

    # 初始状态：仅根目录配置了路由规则
    text = render_upload_toml({
        "chat_id": -100999,
        "watch_enabled": True,
        "observer_roots": [
            {"path": str(root_dir), "enabled": True},
        ],
        "routes": [
            {
                "name": "download",
                "path": str(root_dir),
                "chat_id": -100999,
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

    toggle_route_fn = find_endpoint("/api/settings/routes/toggle", "POST")

    # 1. 初始检查：子目录继承自父级规则
    route_info = inspect_path_route(sub_dir, hub.get())
    assert route_info["matched"] is True
    assert route_info["is_inherited"] is True
    assert route_info["is_explicit"] is False

    policy = IngestPolicy()
    decision_initial = policy.decide(video, hub.get())
    assert decision_initial.allowed is True

    # 2. 第一次切换：点击禁用子目录 -> 自动生成 auto_blocked 规则就地阻断
    res1 = await toggle_route_fn(TogglePathPayload(path=str(sub_dir)))
    assert res1["path"] == str(sub_dir)
    assert len(res1["routes"]) == 2  # 父级 + 新增的子级
    blocked_route = next(r for r in res1["routes"] if Path(r["path"]).resolve() == sub_dir.resolve())
    assert blocked_route["enabled"] is False
    assert blocked_route["auto_blocked"] is True
    assert blocked_route["chat_id"] == -100999  # 继承了父级目标，且非空落盘

    # 验证磁盘持久化 upload.toml 写入且加载无误
    settings_after_block = hub.get()
    assert len(settings_after_block.routes) == 2
    assert any(r.path.resolve() == sub_dir.resolve() and r.enabled is False and r.auto_blocked is True for r in settings_after_block.routes)

    # 验证决议：子目录下文件被就地阻断
    decision_blocked = policy.decide(video, hub.get())
    assert decision_blocked.allowed is False
    assert decision_blocked.is_disabled is True

    # 3. 第二次切换：点击恢复子目录 -> 自动移除 auto_blocked 临时规则，恢复纯净继承
    res2 = await toggle_route_fn(TogglePathPayload(path=str(sub_dir)))
    assert res2["path"] == str(sub_dir)
    # 子目录规则被完全移除
    assert len(res2["routes"]) == 1
    assert not any(Path(r["path"]).resolve() == sub_dir.resolve() for r in res2["routes"])

    # 验证磁盘与内存中也已移除
    settings_after_unblock = hub.get()
    assert len(settings_after_unblock.routes) == 1
    assert not any(r.path.resolve() == sub_dir.resolve() for r in settings_after_unblock.routes)

    # 验证继承状态恢复：子目录再次继承父级规则
    route_info_restored = inspect_path_route(sub_dir, hub.get())
    assert route_info_restored["matched"] is True
    assert route_info_restored["is_inherited"] is True

    # 验证文件重新允许入库
    decision_restored = policy.decide(video, hub.get())
    assert decision_restored.allowed is True


async def test_custom_route_toggle_preserves_configuration(tmp_path: Path, monkeypatch) -> None:
    """测试用户自定义专属规则（非 auto_blocked）的启停不会删除配置。"""
    monkeypatch.chdir(tmp_path)
    from src.api.app import create_api
    from src.adapters.progress import ProgressHub
    from src.api.routes.settings import TogglePathPayload

    root_dir = tmp_path / "download"
    sub_dir = root_dir / "custom"
    sub_dir.mkdir(parents=True)

    text = render_upload_toml({
        "chat_id": -100,
        "watch_enabled": True,
        "observer_roots": [
            {"path": str(root_dir), "enabled": True},
        ],
        "routes": [
            {
                "name": "custom_name",
                "path": str(sub_dir),
                "chat_id": -100888,
                "enabled": True,
                "caption_template": "my template",
            }
        ],
    })
    cfg_file = tmp_path / "upload.toml"
    cfg_file.write_text(text, encoding="utf-8")
    hub = SettingsHub(cfg_file, tmp_path)

    app = create_api(progress_hub=ProgressHub(), settings_hub=hub)
    toggle_route_fn = None
    for route in app.routes:
        if getattr(route, "path", None) == "/api/settings/routes/toggle" and "POST" in getattr(route, "methods", set()):
            toggle_route_fn = route.endpoint
            break

    # 1. 禁用自定义规则 -> enabled=False，保留规则与参数
    res1 = await toggle_route_fn(TogglePathPayload(path=str(sub_dir)))
    assert len(res1["routes"]) == 1
    assert res1["routes"][0]["enabled"] is False
    assert res1["routes"][0]["caption_template"] == "my template"

    # 2. 重新启用自定义规则 -> enabled=True，依然保留规则与参数（不删除）
    res2 = await toggle_route_fn(TogglePathPayload(path=str(sub_dir)))
    assert len(res2["routes"]) == 1
    assert res2["routes"][0]["enabled"] is True
    assert res2["routes"][0]["caption_template"] == "my template"


async def test_bidirectional_watch_linkage(tmp_path: Path, monkeypatch) -> None:
    """全面测试全局监控总开关与各独立根目录开关的双向联动。"""
    monkeypatch.chdir(tmp_path)
    from src.api.app import create_api
    from src.adapters.progress import ProgressHub
    from src.api.routes.settings import TogglePathPayload

    r1 = tmp_path / "root1"
    r2 = tmp_path / "root2"
    r1.mkdir()
    r2.mkdir()

    cfg_file = tmp_path / "upload.toml"
    cfg_file.write_text(
        render_upload_toml({
            "chat_id": -100,
            "watch_enabled": True,
            "observer_roots": [
                {"path": str(r1), "enabled": True},
                {"path": str(r2), "enabled": True},
            ],
        }),
        encoding="utf-8",
    )
    hub = SettingsHub(cfg_file, tmp_path)
    app = create_api(progress_hub=ProgressHub(), settings_hub=hub)

    def find_endpoint(path: str, method: str):
        for route in app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route.endpoint
        raise KeyError((path, method))

    toggle_global_fn = find_endpoint("/api/settings/watch/toggle", "POST")
    toggle_root_fn = find_endpoint("/api/settings/roots/toggle", "POST")

    # 初始状态：全局 ON，r1 ON, r2 ON
    assert hub.get().watch_enabled is True
    assert all(r.enabled is True for r in hub.get().observer_roots)

    # 1. 下行联动：全局关 -> 所有根目录全关
    g_res1 = await toggle_global_fn()
    assert g_res1["watch_enabled"] is False
    assert hub.get().watch_enabled is False
    assert all(r.enabled is False for r in hub.get().observer_roots)
    assert all(r["enabled"] is False for r in g_res1["observer_roots"])

    # 2. 下行联动：全局开 -> 所有根目录全开
    g_res2 = await toggle_global_fn()
    assert g_res2["watch_enabled"] is True
    assert hub.get().watch_enabled is True
    assert all(r.enabled is True for r in hub.get().observer_roots)
    assert all(r["enabled"] is True for r in g_res2["observer_roots"])

    # 3. 上行联动：关闭其中一个根目录 r1，由于 r2 仍开启，全局仍保持开启 (active_count = 1)
    r_res1 = await toggle_root_fn(TogglePathPayload(path=str(r1)))
    assert r_res1["watch_enabled"] is True
    assert hub.get().watch_enabled is True
    r_map1 = {r.path.resolve(): r.enabled for r in hub.get().observer_roots}
    assert r_map1[r1.resolve()] is False
    assert r_map1[r2.resolve()] is True

    # 4. 上行联动：继续关闭最后一个根目录 r2 (active_count = 0)，全局自动变为关闭
    r_res2 = await toggle_root_fn(TogglePathPayload(path=str(r2)))
    assert r_res2["watch_enabled"] is False
    assert hub.get().watch_enabled is False
    r_map2 = {r.path.resolve(): r.enabled for r in hub.get().observer_roots}
    assert r_map2[r1.resolve()] is False
    assert r_map2[r2.resolve()] is False

    # 5. 上行联动：从全关状态下，重新开启任意一个根目录 r2 -> 全局自动激活开启 (active_count = 1)
    r_res3 = await toggle_root_fn(TogglePathPayload(path=str(r2)))
    assert r_res3["watch_enabled"] is True
    assert hub.get().watch_enabled is True
    r_map3 = {r.path.resolve(): r.enabled for r in hub.get().observer_roots}
    assert r_map3[r1.resolve()] is False
    assert r_map3[r2.resolve()] is True




