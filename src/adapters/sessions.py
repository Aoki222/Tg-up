"""Telegram session 池。

sessions/ 下每个 *.session 对应一个 worker，文件名（不含后缀）即 worker 名。
本模块只负责连上/断开 Client，不创建 UploadWorker（那是 application 的事）。

Telethon 的构造参数要的是「不带 .session 的路径」，它会自己拼后缀。
未授权的 session 会断开并跳过，不放进池里。
"""

from __future__ import annotations

from pathlib import Path

from telethon import TelegramClient

from ..logger import get_logger

logger = get_logger(__name__)


def list_session_files(session_dir: Path) -> dict[str, Path]:
    """扫描 sessions/*.session；stem 即 worker 名。

    返回值去掉 .session 后缀：Telethon 自己会再拼回去。
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    return {
        path.stem: path.with_suffix("")
        for path in sorted(session_dir.glob("*.session"))
    }


class SessionPool:
    """按 sessions/ 目录动态持有已连接的 TelegramClient。"""

    def __init__(self, session_dir: Path, api_id: int, api_hash: str):
        self.session_dir = session_dir
        self.api_id = api_id
        self.api_hash = api_hash
        self.clients: dict[str, TelegramClient] = {}

    def list_files(self) -> dict[str, Path]:
        return list_session_files(self.session_dir)

    def any_client(self) -> TelegramClient | None:
        """给话题创建用：哪个账号在线都行。池空时返回 None。"""
        return next(iter(self.clients.values()), None)

    async def ensure_client(self, name: str, session_path: Path) -> TelegramClient | None:
        """已在池里则复用。新文件尝试连接；失败返回 None，下一轮再试。"""
        existing = self.clients.get(name)
        if existing is not None:
            return existing
        client = TelegramClient(str(session_path), self.api_id, self.api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning("[%s] 未授权，跳过: %s", name, session_path)
                await client.disconnect()
                return None
            current_user = await client.get_me()
            logger.info("[%s] 加载成功: %s", name, current_user.username)
            self.clients[name] = client
            return client
        except Exception:
            logger.exception("[%s] 连接失败: %s", name, session_path)
            try:
                await client.disconnect()
            except Exception:
                pass
            return None

    async def remove_client(self, name: str) -> None:
        client = self.clients.pop(name, None)
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            logger.exception("断开客户端失败: %s", name)

    async def disconnect_all(self) -> None:
        for name in list(self.clients):
            await self.remove_client(name)
