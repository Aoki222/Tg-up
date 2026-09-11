"""asyncio 信号量的可调上限版。

普通 Semaphore 创建后容量固定，热更新 concurrency 不会生效。
这里每次 acquire 都重新读 limit_fn()；上限调大时靠 0.5s 超时轮询醒来，
不必等有人 release。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class ConcurrencyGate:
    """上限随回调变化，便于热更新 concurrency。"""

    def __init__(self, limit_fn: Callable[[], int]):
        self._limit_fn = limit_fn
        self._active = 0
        self._condition = asyncio.Condition()

    async def __aenter__(self) -> ConcurrencyGate:
        async with self._condition:
            while self._active >= max(1, self._limit_fn()):
                # 超时再看一遍：concurrency 调大时不会一直等到有人释放
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=0.5)
                except TimeoutError:
                    pass
            self._active += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        async with self._condition:
            self._active -= 1
            self._condition.notify_all()
