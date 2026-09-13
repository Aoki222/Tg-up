"""禁用名单落盘。session 文件还在，扫盘时跳过这些名字，避免禁用后 2 秒又被加载。"""

from __future__ import annotations

import json
from pathlib import Path

from ..logger import get_logger

logger = get_logger(__name__)


class DisabledWorkers:
    def __init__(self, path: Path):
        self.path = path
        self._names: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self._names = set()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self._names = {str(item) for item in raw if str(item).strip()}
            else:
                self._names = set()
        except Exception:
            logger.exception("读取禁用名单失败，当作空名单: %s", self.path)
            self._names = set()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(sorted(self._names), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def __contains__(self, name: str) -> bool:
        return name in self._names

    def names(self) -> frozenset[str]:
        return frozenset(self._names)

    def add(self, name: str) -> None:
        self._names.add(name)
        self.save()

    def discard(self, name: str) -> None:
        if name not in self._names:
            return
        self._names.discard(name)
        self.save()
