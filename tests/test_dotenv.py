from pathlib import Path

from src.config import mask_api_hash, upsert_dotenv


def test_upsert_dotenv_replaces_keys_keeps_comments(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# keep me\nAPI_ID=1\nAPI_HASH=oldhasholdhasholdhash12\nTELEGRAM_PROXY=http://127.0.0.1:7890\n",
        encoding="utf-8",
    )
    upsert_dotenv({"API_ID": "99", "API_HASH": "newhashnewhashnewhash12"}, path)
    text = path.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "API_ID=99" in text
    assert "API_HASH=newhashnewhashnewhash12" in text
    assert "TELEGRAM_PROXY=http://127.0.0.1:7890" in text
    assert "API_ID=1" not in text


def test_upsert_dotenv_empty_hash_not_written_when_omitted(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("API_ID=1\nAPI_HASH=keepthiskeepthiskeepthis\n", encoding="utf-8")
    upsert_dotenv({"API_ID": "2"}, path)
    text = path.read_text(encoding="utf-8")
    assert "API_ID=2" in text
    assert "API_HASH=keepthiskeepthiskeepthis" in text


def test_upsert_dotenv_appends_missing_key(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("API_ID=1\n", encoding="utf-8")
    upsert_dotenv({"API_HASH": "abc"}, path)
    text = path.read_text(encoding="utf-8")
    assert "API_ID=1" in text
    assert "API_HASH=abc" in text


def test_mask_api_hash() -> None:
    assert mask_api_hash("8da85b0d5bfe62527e5b244c209159c3").startswith("8da8")
    assert mask_api_hash("8da85b0d5bfe62527e5b244c209159c3").endswith("c3")
    assert "••••" in mask_api_hash("8da85b0d5bfe62527e5b244c209159c3")
