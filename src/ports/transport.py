"""发送端口。实现可以换成 Bot API，调度/worker 不用改。

五种结果必须分开：
- SendOk：拿到消息 id
- SendRetryLater：FloodWait，任务没坏，账号要歇 seconds 秒
- SendDisconnected：网络/断线，不计业务失败，走连接重试
- SendFailed：文件/权限/超时等，走重试计数。retryable 为假时一次记 failed
- SendOversized：分片超过 Bot 上限，停在 oversized，不换 Bot
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
    """业务失败。源文件已经不在时 retryable 为假，一次记 failed，不再占着账号回队列。"""

    reason: str
    retryable: bool = True


@dataclass(frozen=True)
class SendOversized:
    """文件分片超过 Telegram Bot 上限。停住，不要回 pending 换 Bot。"""

    reason: str


SendResult = SendOk | SendRetryLater | SendDisconnected | SendFailed | SendOversized


class Transport(Protocol):
    async def send(
        self,
        task: Task,
        timeout_seconds: int,
        on_progress: Callable[[float, float], None] | None = None,
    ) -> SendResult:
        ...
