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
