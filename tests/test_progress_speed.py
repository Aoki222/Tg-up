from src.adapters.progress import ProgressHub
from src.domain.progress import is_album_units, make_progress


def _upload(task_id: int, current: float, total: float):
    return make_progress(
        task_id=task_id,
        worker_name="bot",
        file_name="a.mp4",
        current=current,
        total=total,
        stage="uploading",
    )


def test_is_album_units() -> None:
    assert is_album_units(0.4, 2)
    assert is_album_units(2, 2)
    assert not is_album_units(500_000, 1_000_000)
    assert not is_album_units(0, 0)


def test_ema_speed_from_byte_stream(monkeypatch) -> None:
    hub = ProgressHub()
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.adapters.progress.time.monotonic", lambda: clock["t"])

    hub.report(_upload(1, 0, 1_000_000))
    assert hub.snapshot()[0].speed_bps == 0

    clock["t"] += 0.5
    hub.report(_upload(1, 500_000, 1_000_000))
    first = hub.snapshot()[0]
    assert first.speed_bps == 1_000_000
    assert first.eta_seconds == 0.5

    clock["t"] += 0.5
    hub.report(_upload(1, 750_000, 1_000_000))
    second = hub.snapshot()[0]
    # 0.55 * 1_000_000 + 0.45 * 500_000 = 775_000
    assert abs(second.speed_bps - 775_000) < 1
    assert second.eta_seconds > 0


def test_zero_delta_keeps_ema(monkeypatch) -> None:
    hub = ProgressHub()
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.adapters.progress.time.monotonic", lambda: clock["t"])
    hub.report(_upload(1, 0, 1_000_000))
    clock["t"] += 0.5
    hub.report(_upload(1, 500_000, 1_000_000))
    clock["t"] += 0.5
    hub.report(_upload(1, 500_000, 1_000_000))
    assert hub.snapshot()[0].speed_bps == 1_000_000


def test_album_units_have_no_byte_speed(monkeypatch) -> None:
    hub = ProgressHub()
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.adapters.progress.time.monotonic", lambda: clock["t"])
    hub.report(_upload(1, 0.2, 2))
    clock["t"] += 0.5
    hub.report(_upload(1, 0.8, 2))
    snap = hub.snapshot()[0]
    assert snap.speed_bps == 0
    assert snap.eta_seconds == -1


def test_unit_change_resets_speed(monkeypatch) -> None:
    hub = ProgressHub()
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.adapters.progress.time.monotonic", lambda: clock["t"])
    hub.report(_upload(1, 0, 1_000_000))
    clock["t"] += 0.5
    hub.report(_upload(1, 500_000, 1_000_000))
    assert hub.snapshot()[0].speed_bps == 1_000_000

    clock["t"] += 0.5
    hub.report(_upload(1, 0.4, 2))
    snap = hub.snapshot()[0]
    assert snap.speed_bps == 0
    assert snap.total == 2


def test_terminal_stage_clears_snapshot(monkeypatch) -> None:
    hub = ProgressHub()
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.adapters.progress.time.monotonic", lambda: clock["t"])
    hub.report(_upload(1, 0, 100))
    hub.report(
        make_progress(
            task_id=1,
            worker_name="bot",
            file_name="a.mp4",
            current=1,
            total=1,
            stage="success",
        )
    )
    assert hub.snapshot() == []
