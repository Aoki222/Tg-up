"""上传成功后的本地收尾端口。实现按 task.policy.after_success 决定怎么处理文件。"""

from __future__ import annotations

from typing import Protocol

from ..domain.task import Task


class AfterUpload(Protocol):
    async def handle(self, task: Task) -> None:
        """上传成功后的本地收尾。行为由 task.policy.after_success 决定。"""
        ...
