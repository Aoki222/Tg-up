"""发送端口。实现可以换成 Bot API，调度/worker 不用改。

三种结果必须分开：
- SendOk：拿到消息 id
- SendRetryLater：FloodWait，任务没坏，账号要歇 seconds 秒
- SendFailed：文件/权限/超时等，走重试计数
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.task import Task


@dataclass(frozen=True)
class SendOk:
    message_id: int


@dataclass(frozen=True)
class SendRetryLater:
    """FloodWait：服务器要求等待 seconds 秒。不计业务失败、不增加 retry_count。"""

    seconds: int


@dataclass(frozen=True)
class SendFailed:
    reason: str


SendResult = SendOk | SendRetryLater | SendFailed


class Transport(Protocol):
    async def send(self, task: Task, timeout_seconds: int) -> SendResult:
        ...
