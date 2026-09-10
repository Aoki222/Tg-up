from __future__ import annotations

from typing import Protocol

from ..domain.task import Task


class AfterUpload(Protocol):
    async def handle(self, task: Task) -> None:
        """上传成功后的本地收尾：保留 / 删除 / 归档。"""
        ...
