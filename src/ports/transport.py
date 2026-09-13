"""发送端口。实现可以换成 Bot API，调度/worker 不用改。

四种结果必须分开：
- SendOk：拿到消息 id
- SendRetryLater：FloodWait，任务没坏，账号要歇 seconds 秒
- SendDisconnected：网络/断线，不计业务失败，走连接重试
- SendFailed：文件/权限/超时等，走重试计数
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
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
class SendDisconnected:
    """Telegram client 断线或连不上。不计 retry_count，由连接守卫重试。"""

    reason: str


@dataclass(frozen=True)
class SendFailed:
    reason: str


SendResult = SendOk | SendRetryLater | SendDisconnected | SendFailed


class Transport(Protocol):
    async def send(
        self,
        task: Task,
        timeout_seconds: int,
        on_progress: Callable[[float, float], None] | None = None,
    ) -> SendResult:
        ...
