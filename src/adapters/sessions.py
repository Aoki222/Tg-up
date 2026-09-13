"""Telegram session 池。

sessions/ 下每个 *.session 对应一个 worker，文件名（不含后缀）即 worker 名。
本模块只负责连上/断开 Client，不创建 UploadWorker（那是 application 的事）。

断线或连不上：最多试 5 次，等待 1→2→4→8→16 秒，上限 30 秒；5 次仍失败再等 30 秒开新一轮。
try_connect 不 sleep，避免卡住 Application 的 session 锁。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient

from ..logger import get_logger

logger = get_logger(__name__)

RECONNECT_MAX_ATTEMPTS = 5
RECONNECT_MAX_WAIT = 30.0


def list_session_files(session_dir: Path) -> dict[str, Path]:
    """扫描 sessions/*.session；stem 即 worker 名。

    返回值去掉 .session 后缀：Telethon 自己会再拼回去。
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    return {
        path.stem: path.with_suffix("")
        for path in sorted(session_dir.glob("*.session"))
        if not path.stem.startswith("_tmp_")
    }


def reconnect_wait_seconds(attempt: int) -> float:
    """第 attempt 次失败后的等待。attempt 从 1 计。"""
    if attempt >= RECONNECT_MAX_ATTEMPTS:
        return RECONNECT_MAX_WAIT
    return min(RECONNECT_MAX_WAIT, float(2 ** (attempt - 1)))


@dataclass
class ReconnectStatus:
    attempt: int = 0
    next_at: float = 0.0
    error: str = ""
    auth_failed: bool = False

    def snapshot(self) -> dict:
        wait = max(0.0, self.next_at - time.monotonic())
        reconnecting = (not self.auth_failed) and (self.attempt > 0 or bool(self.error))
        return {
            "reconnecting": reconnecting,
            "reconnect_attempt": self.attempt,
            "reconnect_max": RECONNECT_MAX_ATTEMPTS,
            "reconnect_wait_seconds": round(wait, 1),
            "reconnect_error": self.error,
        }


class SessionPool:
    """按 sessions/ 目录动态持有已连接的 TelegramClient。"""

    def __init__(self, session_dir: Path, api_id: int, api_hash: str):
        self.session_dir = session_dir
        self.api_id = api_id
        self.api_hash = api_hash
        self.clients: dict[str, TelegramClient] = {}
        self.usernames: dict[str, str | None] = {}
        self.reconnect: dict[str, ReconnectStatus] = {}
        self._connect_lock = asyncio.Lock()

    def list_files(self) -> dict[str, Path]:
        return list_session_files(self.session_dir)

    def any_client(self) -> TelegramClient | None:
        """给话题创建用：哪个账号在线都行。池空时返回 None。"""
        for name, client in self.clients.items():
            if client.is_connected() and not self.is_reconnecting(name):
                return client
        return next(iter(self.clients.values()), None)

    def reconnect_snapshot(self, name: str) -> dict:
        status = self.reconnect.get(name)
        if status is None:
            return ReconnectStatus().snapshot()
        return status.snapshot()

    def is_reconnecting(self, name: str) -> bool:
        status = self.reconnect.get(name)
        if status is None or status.auth_failed:
            return False
        if status.attempt > 0 or status.error:
            return True
        client = self.clients.get(name)
        return client is not None and not client.is_connected()

    def can_retry_now(self, name: str) -> bool:
        status = self.reconnect.get(name)
        if status is None or status.auth_failed:
            return True
        return time.monotonic() >= status.next_at

    def mark_disconnected(self, name: str, error: str) -> None:
        status = self.reconnect.setdefault(name, ReconnectStatus())
        if status.auth_failed:
            return
        if status.attempt <= 0:
            status.attempt = 1
            status.next_at = time.monotonic()
        status.error = error[:300]

    async def ensure_client(self, name: str, session_path: Path) -> TelegramClient | None:
        """已在池里且已连接则复用。未到重试点则返回已有 client 或 None，不阻塞。"""
        async with self._connect_lock:
            return await self._ensure_client_locked(name, session_path)

    async def _ensure_client_locked(self, name: str, session_path: Path) -> TelegramClient | None:
        status = self.reconnect.setdefault(name, ReconnectStatus())
        if status.auth_failed:
            return None
        existing = self.clients.get(name)
        if existing is not None and existing.is_connected():
            status.attempt = 0
            status.error = ""
            status.next_at = 0.0
            return existing
        if not self.can_retry_now(name):
            return existing
        if status.attempt >= RECONNECT_MAX_ATTEMPTS and time.monotonic() >= status.next_at:
            status.attempt = 0
        status.attempt += 1
        client = existing or TelegramClient(str(session_path), self.api_id, self.api_hash)
        try:
            if not client.is_connected():
                await asyncio.wait_for(client.connect(), timeout=8)
            if not await asyncio.wait_for(client.is_user_authorized(), timeout=8):
                status.auth_failed = True
                status.error = "session 未授权"
                logger.warning("[%s] 未授权, 跳过: %s", name, session_path)
                await client.disconnect()
                self.clients.pop(name, None)
                return None
            current_user = await asyncio.wait_for(client.get_me(), timeout=8)
            username = getattr(current_user, "username", None)
            self.clients[name] = client
            self.usernames[name] = username
            status.attempt = 0
            status.error = ""
            status.next_at = 0.0
            logger.info("[%s] 连接成功: %s", name, username)
            return client
        except Exception as error:
            wait = reconnect_wait_seconds(status.attempt)
            status.error = str(error)[:300]
            status.next_at = time.monotonic() + wait
            logger.warning(
                "[%s] 连接失败 %s/%s，%.0fs 后重试: %s",
                name,
                status.attempt,
                RECONNECT_MAX_ATTEMPTS,
                wait,
                error,
            )
            if existing is None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return existing if existing is not None and existing.is_connected() else None

    async def remove_client(self, name: str) -> None:
        """从池里拿掉并 disconnect。Application 会先停对应 Worker。"""
        client = self.clients.pop(name, None)
        self.usernames.pop(name, None)
        self.reconnect.pop(name, None)
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=2)
        except Exception:
            logger.exception("断开客户端失败: %s", name)

    async def disconnect_all(self) -> None:
        for name in list(self.clients):
            await self.remove_client(name)
