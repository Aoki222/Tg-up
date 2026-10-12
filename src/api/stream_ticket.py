"""SSE 短时票据。EventSource 不能带 Authorization，也不再把长期 API_TOKEN 放进查询参数。"""

from __future__ import annotations

import secrets
import time
from threading import Lock


class StreamTickets:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl_seconds = ttl_seconds
        self._expires: dict[str, float] = {}
        self._lock = Lock()

    def issue(self) -> tuple[str, int]:
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._drop_expired(now)
            self._expires[token] = now + self.ttl_seconds
        return token, self.ttl_seconds

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            self._drop_expired(now)
            deadline = self._expires.get(token)
            return deadline is not None and deadline >= now

    def _drop_expired(self, now: float) -> None:
        stale = [key for key, deadline in self._expires.items() if deadline < now]
        for key in stale:
            self._expires.pop(key, None)
