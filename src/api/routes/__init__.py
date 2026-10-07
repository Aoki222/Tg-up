"""HTTP 路由分组。每个模块只注册自己的路径，不写 SQL，也不直接调用 Telegram。"""

from .account import register as register_account
from .chats import register as register_chats
from .settings import register as register_settings
from .system import register as register_system
from .tasks import register as register_tasks
from .workers import register as register_workers

__all__ = [
    "register_account",
    "register_chats",
    "register_settings",
    "register_system",
    "register_tasks",
    "register_workers",
]
