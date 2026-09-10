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
