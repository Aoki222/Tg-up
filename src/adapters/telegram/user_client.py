"""管理群和话题时只拿个人号。

机器人即使用 MTProto 登录，也列不出全部话题。
发送文件仍由各自的 Worker 完成，不经过这里。
"""

from __future__ import annotations

from telethon import TelegramClient


def connected_user_client(pool) -> TelegramClient | None:
    """返回一个在线、且不是机器人的客户端。没有就返回 None。"""
    kinds = getattr(pool, "kinds", {})
    clients = getattr(pool, "clients", {})
    for name, client in clients.items():
        if kinds.get(name) != "user":
            continue
        if not client.is_connected():
            continue
        is_reconnecting = getattr(pool, "is_reconnecting", None)
        if callable(is_reconnecting) and is_reconnecting(name):
            continue
        return client
    return None
