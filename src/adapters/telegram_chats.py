"""从当前 Session 列出可投递的群/频道。"""

from __future__ import annotations

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from telethon.utils import get_peer_id

from ..logger import get_logger

logger = get_logger(__name__)


async def resolve_chat_title(client: TelegramClient, chat_id: int) -> str:
    entity = await client.get_entity(chat_id)
    return str(getattr(entity, "title", None) or getattr(entity, "username", None) or "")


async def list_dialog_chats(client: TelegramClient) -> list[dict]:
    """只收群和频道，id 与 upload.toml 的 chat_id 同一种（含 -100 前缀）。"""
    items: list[dict] = []
    seen: set[int] = set()
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, (Channel, Chat)):
            continue
        chat_id = int(get_peer_id(entity))
        if chat_id in seen:
            continue
        seen.add(chat_id)
        items.append({"id": chat_id, "title": dialog.name or ""})
    return items
