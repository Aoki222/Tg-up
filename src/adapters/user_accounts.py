"""个人号的两档开关：是否保持连接，是否加入上传队列。

缺省是连着、不上传。机器人不写这个文件。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..logger import get_logger

logger = get_logger(__name__)


class UserAccountPrefs:
    def __init__(self, path: Path):
        self.path = path
        self._items: dict[str, dict[str, bool]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self._items = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("读取个人号开关失败，当作空表: %s", self.path)
            self._items = {}
            return
        items: dict[str, dict[str, bool]] = {}
        if isinstance(raw, dict):
            for name, value in raw.items():
                if not isinstance(value, dict):
                    continue
                items[str(name)] = {
                    "closed": bool(value.get("closed")),
                    "upload": bool(value.get("upload")),
                }
        self._items = items

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def closed(self, name: str) -> bool:
        return bool(self._items.get(name, {}).get("closed", False))

    def uploads(self, name: str) -> bool:
        row = self._items.get(name, {})
        if row.get("closed"):
            return False
        return bool(row.get("upload", False))

    def set_closed(self, name: str, closed: bool) -> None:
        row = self._items.setdefault(name, {"closed": False, "upload": False})
        row["closed"] = closed
        if closed:
            row["upload"] = False
        self.save()

    def set_upload(self, name: str, upload: bool) -> None:
        row = self._items.setdefault(name, {"closed": False, "upload": False})
        row["upload"] = upload
        if upload:
            row["closed"] = False
        self.save()

    def discard(self, name: str) -> None:
        if name not in self._items:
            return
        self._items.pop(name, None)
        self.save()
