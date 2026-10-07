"""从个人号列出可投递的群和频道。

机器人列不出用户加入的全部对话，调用方必须先跳过机器人。
返回的 id 已是带 -100 前缀的 chat_id，和 upload.toml 里存的一致。
"""

from __future__ import annotations

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from telethon.utils import get_peer_id

from ...logger import get_logger

logger = get_logger(__name__)


async def resolve_chat_title(client: TelegramClient, chat_id: int) -> str:
    entity = await client.get_entity(chat_id)
    return str(getattr(entity, "title", None) or getattr(entity, "username", None) or "")


async def list_dialog_chats(client: TelegramClient) -> list[dict]:
    """只收群和频道。私聊不进入投递列表。"""
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
        if isinstance(entity, Channel) and not getattr(entity, "megagroup", False):
            kind = "channel"
            forum = False
        else:
            kind = "group"
            forum = bool(getattr(entity, "forum", False))
        items.append(
            {
                "id": chat_id,
                "title": dialog.name or "",
                "type": kind,
                "username": str(getattr(entity, "username", None) or ""),
                "forum": forum,
            }
        )
    logger.debug("列出对话 %s 个", len(items))
    return items
