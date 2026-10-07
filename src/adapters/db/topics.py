"""chat_topic 表：一个群加一个目录路径，对应一个话题编号。

只记自动创建出来的话题。路径上指定的话题编号写在路由配置里，不进这张表。
"""

from __future__ import annotations

from ...database.connection import get_db


async def get_chat_topic(chat_id: int, topic_path: str) -> int | None:
    """topic_path 是目录绝对路径。同一群同一目录复用 topic_id。"""
    async with get_db() as database:
        async with database.execute(
            "SELECT topic_id FROM chat_topic WHERE chat_id = ? AND topic_path = ?",
            (chat_id, topic_path),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else None


async def save_chat_topic(chat_id: int, topic_id: int, topic_path: str) -> None:
    """重复的 (chat_id, topic_path) 更新 topic_id 和时间戳。"""
    async with get_db() as database:
        await database.execute(
            """INSERT INTO chat_topic (chat_id, topic_id, topic_path)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id, topic_path) DO UPDATE SET
                   topic_id = excluded.topic_id,
                   updated_at = CURRENT_TIMESTAMP""",
            (chat_id, topic_id, topic_path),
        )
        await database.commit()


async def list_topics(chat_id: int) -> list[dict]:
    """这个群里仍有效的话题名单。没有记录表示还没刷新过。"""
    async with get_db() as database:
        async with database.execute(
            """SELECT topic_id, title FROM telegram_topics
               WHERE chat_id = ? AND is_active = 1
               ORDER BY title COLLATE NOCASE, topic_id""",
            (chat_id,),
        ) as cursor:
            return [
                {"topic_id": int(row[0]), "title": str(row[1] or "")}
                for row in await cursor.fetchall()
            ]


async def replace_topics(account_name: str, chat_id: int, topics: list[dict]) -> None:
    """用一次 Telegram 刷新覆盖这个群的话题名单。"""
    async with get_db() as database:
        await database.execute(
            "UPDATE telegram_topics SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (chat_id,),
        )
        await database.executemany(
            """INSERT INTO telegram_topics (account_name, chat_id, topic_id, title, is_active)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(chat_id, topic_id) DO UPDATE SET
                   account_name = excluded.account_name,
                   title = excluded.title,
                   is_active = 1,
                   updated_at = CURRENT_TIMESTAMP""",
            [
                (account_name, chat_id, int(item["topic_id"]), str(item.get("title") or ""))
                for item in topics
            ],
        )
        await database.commit()


async def upsert_topic(account_name: str, chat_id: int, topic_id: int, title: str) -> None:
    """新建或改名后只更新这一行，避免再拉整群。"""
    async with get_db() as database:
        await database.execute(
            """INSERT INTO telegram_topics (account_name, chat_id, topic_id, title, is_active)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(chat_id, topic_id) DO UPDATE SET
                   account_name = excluded.account_name,
                   title = excluded.title,
                   is_active = 1,
                   updated_at = CURRENT_TIMESTAMP""",
            (account_name, chat_id, topic_id, title),
        )
        await database.commit()


async def deactivate_topic(chat_id: int, topic_id: int) -> None:
    async with get_db() as database:
        await database.execute(
            """UPDATE telegram_topics SET is_active = 0, updated_at = CURRENT_TIMESTAMP
               WHERE chat_id = ? AND topic_id = ?""",
            (chat_id, topic_id),
        )
        await database.execute(
            "DELETE FROM chat_topic WHERE chat_id = ? AND topic_id = ?",
            (chat_id, topic_id),
        )
        await database.commit()
