from src.api.app import api_token_ok


def test_api_token_open_when_unset(monkeypatch) -> None:
    monkeypatch.setattr("src.api.app.API_TOKEN", None)
    assert api_token_ok(None, None)
    assert api_token_ok("Bearer x", "y")


def test_api_token_accepts_bearer_or_query(monkeypatch) -> None:
    monkeypatch.setattr("src.api.app.API_TOKEN", "secret")
    assert api_token_ok("Bearer secret", None)
    assert api_token_ok(None, "secret")
    assert api_token_ok("Bearer secret", "secret")
    assert not api_token_ok(None, None)
    assert not api_token_ok("Bearer other", None)
    assert not api_token_ok(None, "other")
    assert not api_token_ok("secret", None)
