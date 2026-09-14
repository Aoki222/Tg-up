"""启动扫盘：watchdog 看不到进程起来之前就已经在目录里的文件。"""

from __future__ import annotations

from pathlib import Path


def iter_existing_files(root: Path, extensions: frozenset[str]) -> list[Path]:
    if not root.exists():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and (not extensions or path.suffix.lower() in extensions)
    ]


def iter_existing_files_many(roots: list[Path] | tuple[Path, ...], extensions: frozenset[str]) -> list[Path]:
    seen: set[str] = set()
    found: list[Path] = []
    for root in roots:
        for path in iter_existing_files(root, extensions):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            found.append(path)
    return found
