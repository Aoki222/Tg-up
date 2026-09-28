"""Telegram 群组/频道本地缓存。

SQLite 是跨重启的缓存真相源，内存字典负责 API 的快速读取。
全量同步只在首次发现账号或手动刷新时访问 Telegram；正常查询不访问网络。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat
from telethon.utils import get_peer_id

from ..database.connection import get_db
from .telegram_chats import list_dialog_chats


class TelegramChannelCache:
    """按个人账号缓存可见群组/频道，并提供全量同步和内存快照。"""

    def __init__(self) -> None:
        self._items: dict[str, dict[int, dict[str, Any]]] = {}
        self._synced_accounts: set[str] = set()
        self._lock = asyncio.Lock()
        self._handlers: dict[str, tuple[TelegramClient, object]] = {}
        self._account_user_ids: dict[str, int] = {}

    async def load_from_db(self) -> None:
        """启动时一次性读取本地表，之后 API 直接读取内存。"""
        async with get_db() as db:
            async with db.execute(
                """SELECT account_name, chat_id, title, type, username, is_active
                   FROM telegram_channels
                   ORDER BY title COLLATE NOCASE, chat_id"""
            ) as cursor:
                rows = await cursor.fetchall()
        self._items.clear()
        self._synced_accounts.clear()
        for row in rows:
            account = str(row[0])
            self._items.setdefault(account, {})[int(row[1])] = self._row_to_item(row)
        self._synced_accounts.update(self._items)

    def has_account(self, account_name: str) -> bool:
        """判断该账号是否已经完成过一次同步，允许空列表账号只同步一次。"""
        return account_name in self._synced_accounts

    def snapshot(self, account_name: str) -> list[dict[str, Any]]:
        """返回指定个人号的 active 群组/频道快照，不触发 Telegram 请求。"""
        items = self._items.get(account_name, {}).values()
        return [dict(item) for item in items if item["is_active"]]

    async def full_sync(self, account_name: str, client: TelegramClient) -> list[dict[str, Any]]:
        """从 Telegram 全量读取账号会话，覆盖本地 active 状态并刷新内存。"""
        async with self._lock:
            items = await list_dialog_chats(client)
            await self._replace_account(account_name, items)
            self._synced_accounts.add(account_name)
            return self.snapshot(account_name)

    async def attach_client(self, account_name: str, client: TelegramClient) -> None:
        """给个人号注册 ChatAction 增量监听，重复加载同一客户端时保持幂等。"""
        current = self._handlers.get(account_name)
        if current is not None and current[0] is client:
            return
        if current is not None:
            current[0].remove_event_handler(current[1], events.ChatAction)
        me = await client.get_me()
        if me is not None:
            self._account_user_ids[account_name] = int(me.id)

        async def handle(event) -> None:
            await self.handle_chat_action(account_name, event)

        client.add_event_handler(handle, events.ChatAction)
        self._handlers[account_name] = (client, handle)

    def detach_client(self, account_name: str) -> None:
        """解除个人号事件监听，避免客户端卸载后继续写缓存。"""
        current = self._handlers.pop(account_name, None)
        self._account_user_ids.pop(account_name, None)
        if current is not None:
            current[0].remove_event_handler(current[1], events.ChatAction)

    async def handle_chat_action(self, account_name: str, event: Any) -> None:
        """将 ChatAction 中携带的群组变化增量写入本地表和内存。"""
        entity = getattr(event, "chat", None)
        if entity is None:
            try:
                entity = await event.get_chat()
            except Exception:
                return
        if not isinstance(entity, (Channel, Chat)):
            return
        chat_id = int(getattr(event, "chat_id", 0) or get_peer_id(entity))
        is_self_action = getattr(event, "user_id", None) == self._account_user_ids.get(account_name)
        left = bool(getattr(event, "user_left", False) or getattr(event, "user_kicked", False))
        if is_self_action and left:
            await self.mark_inactive(account_name, chat_id)
            return
        await self.upsert(
            account_name,
            {
                "id": chat_id,
                "title": getattr(entity, "title", None) or "",
                "type": "channel" if isinstance(entity, Channel) and not getattr(entity, "megagroup", False) else "group",
                "username": getattr(entity, "username", None) or "",
            },
        )

    async def upsert(self, account_name: str, item: dict[str, Any]) -> None:
        """增量写入单个群组/频道，并同步更新内存快照。"""
        chat_id = int(item["id"])
        normalized = {
            "id": chat_id,
            "title": str(item.get("title") or ""),
            "type": str(item.get("type") or "group"),
            "username": str(item.get("username") or ""),
            "is_active": bool(item.get("is_active", True)),
        }
        async with self._lock:
            async with get_db() as db:
                await db.execute(
                    """INSERT INTO telegram_channels
                       (account_name, chat_id, title, type, username, is_active)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(account_name, chat_id) DO UPDATE SET
                           title = excluded.title,
                           type = excluded.type,
                           username = excluded.username,
                           is_active = excluded.is_active,
                           updated_at = CURRENT_TIMESTAMP,
                           synced_at = CURRENT_TIMESTAMP""",
                    (
                        account_name,
                        chat_id,
                        normalized["title"],
                        normalized["type"],
                        normalized["username"],
                        int(normalized["is_active"]),
                    ),
                )
                await db.commit()
            self._items.setdefault(account_name, {})[chat_id] = normalized
            self._synced_accounts.add(account_name)

    async def mark_inactive(self, account_name: str, chat_id: int) -> None:
        """将事件确认已离开的群组标记 inactive，而不是立即删除历史记录。"""
        async with self._lock:
            async with get_db() as db:
                await db.execute(
                    """UPDATE telegram_channels
                       SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                       WHERE account_name = ? AND chat_id = ?""",
                    (account_name, chat_id),
                )
                await db.commit()
            item = self._items.get(account_name, {}).get(int(chat_id))
            if item is not None:
                item["is_active"] = False

    async def _replace_account(self, account_name: str, items: Iterable[dict[str, Any]]) -> None:
        normalized_items = []
        memory_items: dict[int, dict[str, Any]] = {}
        for item in items:
            chat_id = int(item["id"])
            normalized = {
                "id": chat_id,
                "title": str(item.get("title") or ""),
                "type": str(item.get("type") or "group"),
                "username": str(item.get("username") or ""),
                "is_active": True,
            }
            normalized_items.append(normalized)
            memory_items[chat_id] = normalized
        async with get_db() as db:
            await db.execute(
                """UPDATE telegram_channels
                   SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                   WHERE account_name = ?""",
                (account_name,),
            )
            await db.executemany(
                """INSERT INTO telegram_channels
                   (account_name, chat_id, title, type, username, is_active)
                   VALUES (?, ?, ?, ?, ?, 1)
                   ON CONFLICT(account_name, chat_id) DO UPDATE SET
                       title = excluded.title,
                       type = excluded.type,
                       username = excluded.username,
                       is_active = 1,
                           updated_at = CURRENT_TIMESTAMP,
                           synced_at = CURRENT_TIMESTAMP""",
                [
                    (
                        account_name,
                        item["id"],
                        item["title"],
                        item["type"],
                        item["username"],
                    )
                    for item in normalized_items
                ],
            )
            await db.commit()
        self._items[account_name] = memory_items

    @staticmethod
    def _row_to_item(row: Any) -> dict[str, Any]:
        """将 SQLite 行转换为前端统一的群组/频道项目结构。"""
        return {
            "id": int(row[1]),
            "title": str(row[2] or ""),
            "type": str(row[3] or "group"),
            "username": str(row[4] or ""),
            "is_active": bool(row[5]),
        }
