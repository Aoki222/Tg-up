"""进度汇报接口。实现可以是日志条、内存总线、或以后的消息队列。"""

from __future__ import annotations

from typing import Protocol

from ..domain.progress import UploadProgress


class ProgressReporter(Protocol):
    def report(self, progress: UploadProgress) -> None:
        ...
