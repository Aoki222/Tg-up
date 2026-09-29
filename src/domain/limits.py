"""Telegram 机器人上传的硬上限。

分片最大 512KB，最多 4000 片，所以 Bot 不能传超过 2_097_152_000 字节的文件。
个人号可以再试一次；失败后仍停在 oversized，不换 Bot 重试。
"""

from __future__ import annotations

TELEGRAM_PART_BYTES = 512 * 1024
TELEGRAM_MAX_PARTS = 4000
TELEGRAM_BOT_MAX_BYTES = TELEGRAM_PART_BYTES * TELEGRAM_MAX_PARTS

OVERSIZED_REASON = "超过 2GB，不自动分配给 Bot"
PARTS_INVALID_REASON = "超过 Telegram 单文件分片上限"


def telegram_bot_blocked(file_size: int, platform: str | None = "telegram") -> bool:
    """只有 Telegram 任务才受 Bot 分片上限约束。"""
    kind = (platform or "telegram").strip() or "telegram"
    return kind == "telegram" and int(file_size) > TELEGRAM_BOT_MAX_BYTES
