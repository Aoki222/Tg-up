from pathlib import Path

from src.adapters.session_login import PendingLogin, SessionLoginService, _qr_png


class FakeClient:
    async def disconnect(self) -> None:
        pass


def test_list_accounts_reads_sidecar_and_prefixes(tmp_path: Path) -> None:
    (tmp_path / "user_7.session").write_bytes(b"")
    (tmp_path / "bot_9.session").write_bytes(b"")
    (tmp_path / "alice.session").write_bytes(b"")
    (tmp_path / "alice.json").write_text('{"is_bot": false, "username": "alice"}', encoding="utf-8")
    service = SessionLoginService(tmp_path, 1, "hash")
    kinds = {item["name"]: item["kind"] for item in service.list_accounts()}
    assert kinds["user_7"] == "user"
    assert kinds["bot_9"] == "bot"
    assert kinds["alice"] == "user"


def test_qr_png_is_base64() -> None:
    encoded = _qr_png("tg://login?token=abc")
    assert encoded.startswith("iVBOR")


async def test_cancel_qr_login_deletes_temporary_session(tmp_path: Path) -> None:
    service = SessionLoginService(tmp_path, 1, "hash")
    login_id = "cancel-me"
    tmp_base = tmp_path / f"_tmp_qr_{login_id}"
    session_file = tmp_base.with_suffix(".session")
    session_file.write_bytes(b"temporary")
    service._pending[login_id] = PendingLogin(
        login_id=login_id,
        client=FakeClient(),
        tmp_base=tmp_base,
        phone="",
        group_id=None,
        force=False,
        kind="qr",
    )

    await service.cancel(login_id)

    assert not session_file.exists()
