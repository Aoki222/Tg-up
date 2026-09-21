from pathlib import Path

from src.domain.settings_hub import load_upload_settings, render_upload_toml


def test_load_toml_without_routes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "upload.toml"
    config.write_text(
        'chat_id = -100\nobserver_paths = ["download"]\npreview = "off"\n',
        encoding="utf-8",
    )
    settings = load_upload_settings(config, tmp_path)
    assert settings.routes == ()
    assert settings.chat_id == -100


def test_render_and_load_routes_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    movies = tmp_path / "movies"
    movies.mkdir()
    text = render_upload_toml(
        {
            "chat_id": -100,
            "observer_paths": [str(tmp_path / "download")],
            "preview": "off",
            "topic_creation_enabled": True,
            "after_success": "keep",
            "concurrency": 3,
            "max_retries": 3,
            "upload_timeout_seconds": 1200,
            "assigned_timeout_seconds": 600,
            "stable_timeout_seconds": 30,
            "watch_extensions": ["mp4"],
            "routes": [
                {
                    "name": "电影",
                    "path": str(movies),
                    "chat_id": -100111,
                    "topic_enabled": False,
                },
                {
                    "name": "跟随",
                    "path": str(tmp_path / "anime"),
                    "chat_id": -100222,
                    "topic_enabled": None,
                },
            ],
        }
    )
    config = tmp_path / "upload.toml"
    config.write_text(text, encoding="utf-8")
    settings = load_upload_settings(config, tmp_path)
    assert len(settings.routes) == 2
    by_name = {route.name: route for route in settings.routes}
    assert by_name["电影"].chat_id == -100111
    assert by_name["电影"].topic_enabled is False
    assert by_name["跟随"].topic_enabled is None
    assert "topic_enabled" not in text.split("[[routes]]")[-1] or "跟随" in text
    follow_block = text.split("name = \"跟随\"", 1)[1].split("[[routes]]")[0]
    assert "topic_enabled" not in follow_block


def test_render_chats_and_disabled_route(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    movies = tmp_path / "movies"
    movies.mkdir()
    text = render_upload_toml(
        {
            "chat_id": -100,
            "observer_paths": [str(tmp_path / "download")],
            "preview": "off",
            "topic_creation_enabled": True,
            "after_success": "keep",
            "concurrency": 3,
            "max_retries": 3,
            "upload_timeout_seconds": 1200,
            "assigned_timeout_seconds": 600,
            "stable_timeout_seconds": 30,
            "watch_extensions": ["mp4"],
            "chats": [{"chat_id": -100111, "alias": "电影"}],
            "routes": [
                {
                    "name": "",
                    "path": str(movies),
                    "chat_id": -100111,
                    "topic_enabled": False,
                    "enabled": False,
                }
            ],
        }
    )
    config = tmp_path / "upload.toml"
    config.write_text(text, encoding="utf-8")
    settings = load_upload_settings(config, tmp_path)
    assert settings.chats[0].alias == "电影"
    assert settings.routes[0].enabled is False
    assert "enabled = false" in text
