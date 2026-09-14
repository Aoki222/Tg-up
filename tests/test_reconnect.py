from src.adapters.sessions import RECONNECT_MAX_WAIT, reconnect_wait_seconds


def test_backoff_grows_and_caps_at_30() -> None:
    assert reconnect_wait_seconds(1) == 1.0
    assert reconnect_wait_seconds(2) == 2.0
    assert reconnect_wait_seconds(3) == 4.0
    assert reconnect_wait_seconds(4) == 8.0
    assert reconnect_wait_seconds(5) == RECONNECT_MAX_WAIT
    assert reconnect_wait_seconds(9) == RECONNECT_MAX_WAIT
