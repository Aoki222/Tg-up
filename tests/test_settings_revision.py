"""配置整份保存带 revision；旧页面再保存会冲突。topic_mode 折进 toml。"""

from pathlib import Path

from src.domain.settings_hub import SettingsConflict, SettingsHub, render_upload_toml


def _hub(tmp_path: Path, **extra) -> SettingsHub:
    payload = {
        "chat_id": -100,
        "watch_enabled": True,
        "observer_roots": [{"path": str(tmp_path / "download"), "enabled": True}],
        "caption_template": "hello",
        "routes": [
            {
                "name": "in",
                "path": str(tmp_path / "download"),
                "chat_id": -100,
                "dest_id": "-100",
                "platform": "telegram",
                "topic_enabled": False,
                "enabled": True,
            }
        ],
    }
    payload.update(extra)
    path = tmp_path / "upload.toml"
    path.write_text(render_upload_toml(payload), encoding="utf-8")
    return SettingsHub(path, tmp_path)


def test_stale_revision_is_rejected(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    current = hub.public_dict()
    old = current["revision"]
    current["caption_template"] = "newer"
    saved = hub.save_from_payload(current, expected_revision=old)
    assert saved["revision"] != old
    current["caption_template"] = "stale"
    try:
        hub.save_from_payload(current, expected_revision=old)
    except SettingsConflict as error:
        assert "重新加载" in str(error)
    else:
        raise AssertionError("旧 revision 应该冲突")
    assert hub.public_dict()["caption_template"] == "newer"


def test_topic_enabled_folds_into_topic_mode(tmp_path: Path) -> None:
    raw = tmp_path / "legacy.toml"
    raw.write_text(
        "chat_id = -100\n"
        "[[routes]]\n"
        f'path = "{(tmp_path / "download").as_posix()}"\n'
        "chat_id = -100\n"
        'dest_id = "-100"\n'
        "topic_enabled = false\n",
        encoding="utf-8",
    )
    legacy = SettingsHub(raw, tmp_path)
    assert legacy.get().routes[0].topic_mode == "off"
    assert legacy.public_dict()["routes"][0]["topic_enabled"] is False

    hub = _hub(tmp_path)
    route = hub.public_dict()["routes"][0]
    assert route["topic_mode"] == "off"
    assert route["topic_enabled"] is False
    saved = hub.save_from_payload(hub.public_dict(), expected_revision=hub.public_dict()["revision"])
    text = (tmp_path / "upload.toml").read_text(encoding="utf-8")
    assert 'topic_mode = "off"' in text
    assert "\ntopic_enabled" not in text
    assert saved["routes"][0]["topic_enabled"] is False


def test_missing_schedule_order_follows_discovery(tmp_path: Path) -> None:
    path = tmp_path / "upload.toml"
    path.write_text('chat_id = -100\n', encoding="utf-8")
    hub = SettingsHub(path, tmp_path)
    assert hub.get().schedule_order == "oldest"
    assert hub.public_dict()["schedule_order"] == "oldest"


def test_explicit_smallest_stays_small_first(tmp_path: Path) -> None:
    path = tmp_path / "upload.toml"
    path.write_text('chat_id = -100\nschedule_order = "smallest"\n', encoding="utf-8")
    hub = SettingsHub(path, tmp_path)
    assert hub.get().schedule_order == "smallest"


def test_schedule_order_round_trip(tmp_path: Path) -> None:
    hub = _hub(tmp_path, schedule_order="oldest")
    assert hub.get().schedule_order == "oldest"
    assert hub.public_dict()["schedule_order"] == "oldest"
    payload = hub.public_dict()
    payload["schedule_order"] = "smallest"
    saved = hub.save_from_payload(payload, expected_revision=payload["revision"])
    assert saved["schedule_order"] == "smallest"
    assert 'schedule_order = "smallest"' in (tmp_path / "upload.toml").read_text(encoding="utf-8")


def test_mutate_skips_write_when_cancelled(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    before = hub.public_dict()["revision"]

    def cancel(_current: dict) -> bool:
        return False

    assert hub.mutate(cancel) is None
    assert hub.public_dict()["revision"] == before
