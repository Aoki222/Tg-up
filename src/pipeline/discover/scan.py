"""启动扫盘：watchdog 看不到进程起来之前就已经在目录里的文件。"""

from __future__ import annotations

from pathlib import Path


def iter_existing_files(root: Path, extensions: frozenset[str]) -> list[Path]:
    if not root.exists():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    ]
