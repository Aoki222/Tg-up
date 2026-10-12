"""看板和配置响应先校验，再原样返回。多出来的字段不能被模型丢掉。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.adapters.progress import ProgressHub
from src.adapters.task_store import TaskRepository
from src.api.app import create_api
from src.api.responses import BoardSnapshotModel, SettingsPublicModel, publish
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import SCHEMA
from src.domain.settings_hub import SettingsHub, render_upload_toml


def _route(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise KeyError((path, method))


def test_publish_keeps_unknown_fields() -> None:
    payload = {
        "items": [],
        "counts": {
            "preparing": 0,
            "pending": 0,
            "assigned": 0,
            "uploading": 0,
            "oversized": 0,
            "failed": 0,
            "success": 0,
            "success_today": 0,
            "future_count": 4,
        },
        "future_field": "keep",
    }
    out = publish(BoardSnapshotModel, payload)
    assert out is payload
    assert out["future_field"] == "keep"
    assert out["counts"]["future_count"] == 4


def test_publish_rejects_a_board_missing_dest() -> None:
    with pytest.raises(ValidationError):
        publish(
            BoardSnapshotModel,
            {
                "items": [{"id": 1, "file_name": "a.mp4"}],
                "counts": {
                    "preparing": 0,
                    "pending": 0,
                    "assigned": 0,
                    "uploading": 0,
                    "oversized": 0,
                    "failed": 0,
                    "success": 0,
                    "success_today": 0,
                },
            },
        )


def test_documented_routes_are_not_filtered_by_a_response_model() -> None:
    """返回标注是 dict。字段校验在 publish 里做，FastAPI 不再按模型删键。"""
    app = create_api(ProgressHub())
    for path in ("/api/tasks", "/api/tasks/failed", "/api/tasks/oversized", "/api/settings"):
        assert _route(app, path, "GET").response_model is dict


async def test_live_board_and_settings_pass_validation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    await open_pool(tmp_path / "app.db")
    try:
        async with get_db() as database:
            await database.executescript(SCHEMA)
            await database.commit()
        repo = TaskRepository()
        await repo.add_task(
            file_path=str(tmp_path / "clip.mp4"),
            file_name="clip.mp4",
            file_size=20,
            folder_name="inbox",
            chat_id=-100,
            status="pending",
        )
        failed = await repo.add_task(
            file_path=str(tmp_path / "bad.mp4"),
            file_name="bad.mp4",
            file_size=30,
            folder_name="inbox",
            chat_id=-100,
            status="pending",
            max_retries=1,
        )
        await repo.mark_task_failed(failed, 1, 1, "boom")

        root = tmp_path / "download"
        root.mkdir()
        text = render_upload_toml(
            {
                "chat_id": -100,
                "schedule_order": "oldest",
                "observer_roots": [{"path": str(root), "enabled": True}],
                "routes": [
                    {
                        "name": "inbox",
                        "path": str(root),
                        "chat_id": -100,
                        "platform": "telegram",
                        "dest_id": "-100",
                    }
                ],
            }
        )
        cfg = tmp_path / "upload.toml"
        cfg.write_text(text, encoding="utf-8")
        hub = SettingsHub(cfg, tmp_path)
        app = create_api(ProgressHub(), settings_hub=hub, task_repository=repo)

        board = await _route(app, "/api/tasks", "GET").endpoint()
        assert board["items"][0]["platform"] == "telegram"
        assert board["items"][0]["dest_id"] == "-100"
        assert board["counts"]["pending"] == 1
        assert board["counts"]["failed"] == 1

        failed_page = await _route(app, "/api/tasks/failed", "GET").endpoint(page=1, page_size=50)
        assert failed_page["total"] == 1
        assert failed_page["items"][0]["status"] == "failed"
        assert failed_page["items"][0]["dest_id"] == "-100"

        oversized = await _route(app, "/api/tasks/oversized", "GET").endpoint(page=1, page_size=50)
        assert oversized["total"] == 0
        assert oversized["items"] == []

        settings = await _route(app, "/api/settings", "GET").endpoint()
        assert settings["schedule_order"] == "oldest"
        assert settings["revision"]
        assert settings["routes"][0]["dest_id"] == "-100"
        assert settings["routes"][0]["chat_id"] == -100
        publish(SettingsPublicModel, settings)
    finally:
        await close_pool()
