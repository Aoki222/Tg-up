from pathlib import Path

from src.pipeline.discover.scan import iter_existing_files


def test_empty_extensions_includes_all_files(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"y")
    found = {path.name for path in iter_existing_files(tmp_path, frozenset())}
    assert found == {"a.mp4", "b.pdf"}


def test_named_extensions_filter(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"y")
    found = {path.name for path in iter_existing_files(tmp_path, frozenset({".mp4"}))}
    assert found == {"a.mp4"}


def test_nested_files_stream_without_a_full_list(tmp_path: Path) -> None:
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "c.mp4").write_bytes(b"z")
    (tmp_path / "a.mp4").write_bytes(b"x")
    found = iter_existing_files(tmp_path, frozenset({".mp4"}))
    assert not isinstance(found, list)
    assert {path.name for path in found} == {"a.mp4", "c.mp4"}
