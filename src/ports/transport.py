from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.task import Task


@dataclass(frozen=True)
class SendOk:
    message_id: int


@dataclass(frozen=True)
class SendRetryLater:
    """Telegram FloodWait：任务本身没坏，账号需要歇一段时间。"""

    seconds: int


@dataclass(frozen=True)
class SendFailed:
    reason: str


SendResult = SendOk | SendRetryLater | SendFailed


class Transport(Protocol):
    async def send(self, task: Task, timeout_seconds: int) -> SendResult:
        ...
