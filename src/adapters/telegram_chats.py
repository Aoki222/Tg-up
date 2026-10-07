"""旧导入路径。新代码请用 adapters.telegram.chats。"""

from .telegram.chats import list_dialog_chats, resolve_chat_title

__all__ = ["list_dialog_chats", "resolve_chat_title"]
