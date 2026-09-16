"""主流程：发现 → 入库 → 调度 → 发送。阶段只通过任务状态和队列衔接。

不在这里 import application：装配层会拉起 FastAPI / Telethon，
测试只测 ingest/scan 时不该被带着初始化整个进程。
"""

from typing import Any

__all__ = ["UploaderApplication"]


def __getattr__(name: str) -> Any:
    if name == "UploaderApplication":
        from .application import UploaderApplication

        return UploaderApplication
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
