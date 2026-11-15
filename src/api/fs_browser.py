"""文件系统磁盘扫描与目录缓存服务。

职责：
1. 在独立的后台工作线程中同步读取物理磁盘（避免阻塞主事件循环）；
2. 短 TTL 内存缓存（默认 10 秒），避免前端展开/折叠目录时重复扫盘；
3. In-flight (Singleflight) 请求合并，防止同一瞬间多次请求造成并发扫盘；
4. 纯净解耦：不依赖 FastAPI 路由逻辑、不写 SQL，方便单测与复用。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..pipeline.ingest.policy import path_is_inside_roots


@dataclass(frozen=True)
class RawDiskNode:
    """纯粹的物理磁盘节点信息（不包含业务路由与数据库任务状态）。"""

    path: str
    name: str
    is_dir: bool
    has_children: bool
    size: int = 0
    ext: str = ""
    is_root: bool = False
    exists: bool = True


def _has_sub_children_fast(dir_path: Path) -> bool:
    """轻量判断目录是否有非隐藏子项。碰到第 1 个有效子项立刻退出，不遍历完整目录。"""
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                if not entry.name.startswith("."):
                    return True
    except OSError:
        pass
    return False


def _sync_scan_roots(observer_paths: Sequence[Path]) -> list[RawDiskNode]:
    """同步扫描所有配置的根目录（跑在后台线程中）。"""
    items: list[RawDiskNode] = []
    for root in observer_paths:
        resolved = root.resolve()
        exists = resolved.is_dir()
        has_children = _has_sub_children_fast(resolved) if exists else False
        items.append(
            RawDiskNode(
                path=str(resolved),
                name=resolved.name or str(resolved),
                is_dir=True,
                has_children=has_children,
                exists=exists,
                is_root=True,
            )
        )
    return items


def _sync_scan_dir(req_path: Path, allowed_roots: Sequence[Path]) -> list[RawDiskNode]:
    """同步扫描指定目录下的直接子文件与子文件夹（跑在后台线程中）。"""
    dir_entries: list[os.DirEntry[str]] = []
    file_entries: list[os.DirEntry[str]] = []

    try:
        with os.scandir(req_path) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                try:
                    entry_path = Path(entry.path)
                    if allowed_roots and not path_is_inside_roots(entry_path, allowed_roots):
                        continue

                    if entry.is_dir(follow_symlinks=False):
                        dir_entries.append(entry)
                    elif entry.is_file(follow_symlinks=False):
                        file_entries.append(entry)
                    elif entry.is_symlink():
                        res = entry_path.resolve()
                        if res.is_dir():
                            dir_entries.append(entry)
                        elif res.is_file():
                            file_entries.append(entry)
                except OSError:
                    continue
    except OSError as err:
        raise OSError(f"读取目录失败: {err}") from err

    nodes: list[RawDiskNode] = []

    # 1. 处理子目录（按字母排序）
    for d in sorted(dir_entries, key=lambda e: e.name.lower()):
        d_path = Path(d.path).resolve()
        nodes.append(
            RawDiskNode(
                path=str(d_path),
                name=d.name,
                is_dir=True,
                has_children=_has_sub_children_fast(d_path),
                is_root=False,
            )
        )

    # 2. 处理文件（按字母排序）
    for f in sorted(file_entries, key=lambda e: e.name.lower()):
        try:
            st = f.stat(follow_symlinks=False)
            f_size = st.st_size
        except OSError:
            f_size = 0

        f_path = Path(f.path).resolve()
        ext = f_path.suffix.lstrip(".").lower()

        nodes.append(
            RawDiskNode(
                path=str(f_path),
                name=f.name,
                is_dir=False,
                has_children=False,
                size=f_size,
                ext=ext,
                is_root=False,
            )
        )

    return nodes


class FsBrowserService:
    """带 TTL 缓存与并发去重的异步文件浏览服务。"""

    def __init__(self, ttl_seconds: float = 10.0) -> None:
        self.ttl = max(0.0, float(ttl_seconds))
        # 缓存结构: path_key -> (expire_at, list[RawDiskNode])
        self._cache: dict[str, tuple[float, list[RawDiskNode]]] = {}
        # Singleflight 防击穿锁字典: path_key -> Task
        self._in_flight: dict[str, asyncio.Task[list[RawDiskNode]]] = {}
        self._lock = asyncio.Lock()

    def invalidate(self, path: str | None = None) -> None:
        """主动失效缓存（在用户修改监控路径或添加文件后可选调用）。"""
        if path is None:
            self._cache.clear()
        else:
            self._cache.pop(path, None)

    async def list_roots(self, observer_paths: Sequence[Path]) -> list[RawDiskNode]:
        """异步获取根目录（10秒缓存 + 线程池）。"""
        key = "__ROOTS__"
        return await self._get_or_scan(key, _sync_scan_roots, observer_paths)

    async def list_dir(self, req_path: Path, allowed_roots: Sequence[Path]) -> list[RawDiskNode]:
        """异步获取子目录（10秒缓存 + 线程池）。"""
        key = str(req_path.resolve())
        return await self._get_or_scan(key, _sync_scan_dir, req_path, allowed_roots)

    async def _get_or_scan(self, key: str, sync_fn: Any, *args: Any) -> list[RawDiskNode]:
        now = time.monotonic()

        # 1. 尝试直接命中缓存
        cached = self._cache.get(key)
        if cached is not None and now < cached[0]:
            return cached[1]

        # 2. 检查是否有同样的请求正在后台线程扫描（Singleflight 去重）
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now < cached[0]:
                return cached[1]

            task = self._in_flight.get(key)
            if task is None:
                task = asyncio.create_task(asyncio.to_thread(sync_fn, *args))
                self._in_flight[key] = task

        # 3. 等待后台线程执行完毕
        try:
            result = await task
        finally:
            async with self._lock:
                if self._in_flight.get(key) is task:
                    self._in_flight.pop(key, None)

        # 4. 写入缓存并返回
        self._cache[key] = (time.monotonic() + self.ttl, result)
        return result


# 全局单例
fs_browser = FsBrowserService(ttl_seconds=10.0)
