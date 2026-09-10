from __future__ import annotations

import asyncio
from pathlib import Path

from telethon.tl.functions.messages import CreateForumTopicRequest


from ..logger import get_logger
from ..upload.task_repository import TaskRepository

logger = get_logger(__name__)


class TopicCreator:
	"""按文件目录为群组复用或创建 Telegram 话题。"""

	def __init__(self, telegram_client, task_repository: TaskRepository):
		self.telegram_client = telegram_client
		self.task_repository = task_repository
		self._lock = asyncio.Lock()

	async def get_or_create_topic(
		self, fold_path: str, folder_name: str, chat_id: int
	) -> int:
		"""按群组和目录路径复用话题，目录变化时创建新话题。"""
		topic_path = str(Path(fold_path).resolve())
		async with self._lock:
			existing_topic_id = await self.task_repository.get_chat_topic(chat_id, topic_path)
			if existing_topic_id is not None:
				return existing_topic_id

			result = await self.telegram_client(
				CreateForumTopicRequest(
					peer=chat_id,
					title=folder_name,
				)
			)
			topic_id = self._extract_topic_id(result)
			await self.task_repository.save_chat_topic(chat_id, topic_id, topic_path)
			logger.info("已创建群组话题 chat_id=%s topic_id=%s path=%s", chat_id, topic_id, topic_path)
			return topic_id

	@staticmethod
	def _extract_topic_id(result) -> int:
		for update in getattr(result, "updates", ()):
			message = getattr(update, "message", None)
			message_id = getattr(message, "id", None)
			if message_id is not None:
				return int(message_id)
			update_id = getattr(update, "id", None)
			if update_id is not None:
				return int(update_id)
		raise RuntimeError("Telegram 创建话题成功但未找到 topic_id")
