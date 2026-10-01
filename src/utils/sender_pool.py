"""Telegram MTProto 并行上传连接池（SenderPool）。

职责：
1. 为同一 Telegram 客户端（Worker）管理多条用于分块上传的并行 MTProtoSender 长连接；
2. 借用（acquire）优先复用空闲连接，支持小文件按需借用（如 parts=1 时只借 1 条）；
3. 归还（release）不断开连接，支持存活校验与空闲超时惰性淘汰；
4. 联动断线（invalidate_all）与优雅关机（close），杜绝连接泄漏。
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Callable, Coroutine

from telethon import TelegramClient
from telethon.network import MTProtoSender

from ..logger import get_logger

if TYPE_CHECKING:
    pass

from ..logger import get_logger
from .FastTelethon import create_parallel_sender as default_create_sender

logger = get_logger(__name__)


class SenderPool:
    def __init__(
        self,
        client: TelegramClient,
        max_senders: int = 6,
        idle_timeout: float = 120.0,
        sender_factory: Callable[[TelegramClient], Coroutine[any, any, MTProtoSender]] | None = None,
    ) -> None:
        self.client = client
        self.max_senders = max(1, int(max_senders))
        self.idle_timeout = max(0.01, float(idle_timeout))
        self._sender_factory = sender_factory or default_create_sender

        self._lock = asyncio.Lock()
        self._idle_senders: list[tuple[MTProtoSender, float]] = []
        self._active_senders: set[MTProtoSender] = set()
        self._is_closed = False

    @property
    def idle_count(self) -> int:
        return len(self._idle_senders)

    @property
    def active_count(self) -> int:
        return len(self._active_senders)

    async def acquire(self, count: int) -> list[MTProtoSender]:
        """借用最多 count 条连接，优先从空闲队列获取并检查健康度，不足且未达上限时新建。"""
        if count <= 0:
            return []

        async with self._lock:
            if self._is_closed:
                return []

            now = time.monotonic()
            acquired: list[MTProtoSender] = []

            # 1. 尝试从空闲连接中借用
            while self._idle_senders and len(acquired) < count:
                sender, released_at = self._idle_senders.pop()

                # 检查空闲超时：超时的连接静默释放并淘汰
                if now - released_at > self.idle_timeout:
                    try:
                        await sender.disconnect()
                    except Exception:
                        pass
                    continue

                # 检查连接存活度
                if not sender.is_connected():
                    # 尝试重连一次
                    try:
                        dc = await self.client._get_dc(self.client.session.dc_id)
                        await sender.connect(
                            self.client._connection(
                                dc.ip_address,
                                dc.port,
                                dc.id,
                                loggers=self.client._log,
                                proxy=self.client._proxy,
                                local_addr=getattr(self.client, "_local_addr", None),
                            )
                        )
                    except Exception:
                        try:
                            await sender.disconnect()
                        except Exception:
                            pass
                        continue

                acquired.append(sender)
                self._active_senders.add(sender)

            # 2. 如果不足，且总连接数未达到上限，新建连接补充
            needed = count - len(acquired)
            total_current = len(self._idle_senders) + len(self._active_senders)
            can_create = max(0, self.max_senders - total_current)
            to_create = min(needed, can_create)

            for _ in range(to_create):
                try:
                    sender = await self._sender_factory(self.client)
                    acquired.append(sender)
                    self._active_senders.add(sender)
                except Exception as err:
                    logger.warning("SenderPool 额外上传连接建立失败: %s", err)
                    break

            return acquired

    async def release(self, senders: list[MTProtoSender]) -> None:
        """归还借用的连接，存活且未满的放回空闲队列，损坏或多余的断开回收。"""
        if not senders:
            return

        async with self._lock:
            now = time.monotonic()
            for sender in senders:
                self._active_senders.discard(sender)

                if self._is_closed:
                    try:
                        await sender.disconnect()
                    except Exception:
                        pass
                    continue

                # 存活且容量未满则放回空闲池，否则断开
                if sender.is_connected() and len(self._idle_senders) < self.max_senders:
                    self._idle_senders.append((sender, now))
                else:
                    try:
                        await sender.disconnect()
                    except Exception:
                        pass

    async def invalidate_all(self) -> None:
        """主连接断开或网络异常时调用，将所有空闲连接标记为失效并彻底断开。"""
        async with self._lock:
            to_disconnect = [sender for sender, _ in self._idle_senders]
            self._idle_senders.clear()

        for sender in to_disconnect:
            try:
                await sender.disconnect()
            except Exception:
                pass

    async def close(self) -> None:
        """Worker 卸载或系统停机时调用，彻底关闭池中所有连接。"""
        async with self._lock:
            self._is_closed = True
            all_senders = [s for s, _ in self._idle_senders] + list(self._active_senders)
            self._idle_senders.clear()
            self._active_senders.clear()

        for sender in all_senders:
            try:
                await sender.disconnect()
            except Exception:
                pass
