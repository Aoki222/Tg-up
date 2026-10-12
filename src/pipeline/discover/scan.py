"""启动扫盘：watchdog 看不到进程起来之前就已经在目录里的文件。

遍历逐个产出路径。调用方把路径放进有上限的队列，队列满时遍历会停，
不必先把整个目录收成列表。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path


def _matches(name: str, extensions: frozenset[str]) -> bool:
    if not extensions:
        return True
    return Path(name).suffix.lower() in extensions


def iter_existing_files(root: Path, extensions: frozenset[str]) -> Iterator[Path]:
    """深度优先逐个产出文件。目录用真实路径去重，避免符号链接绕圈。"""
    if not root.exists():
        return
    seen_dirs: set[str] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            identity = str(current.resolve())
        except OSError:
            continue
        if identity in seen_dirs:
            continue
        seen_dirs.add(identity)
        try:
            iterator = os.scandir(current)
        except OSError:
            continue
        with iterator:
            for entry in iterator:
                try:
                    if entry.is_dir(follow_symlinks=True):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=True) and _matches(entry.name, extensions):
                        yield Path(entry.path)
                except OSError:
                    continue


def iter_existing_files_many(
    roots: list[Path] | tuple[Path, ...], extensions: frozenset[str]
) -> Iterator[Path]:
    """多个根依次产出。重叠路径由入库去重，这里不攒完整列表。"""
    for root in roots:
        yield from iter_existing_files(root, extensions)
