from telethon import TelegramClient
import asyncio

from .bot_worker import BotWorker
from .upload_scheduler import UploadScheduler
from ..config import API_HASH, API_ID, SESSION_FILES, OBSERVER_PATH
from ..logger import get_logger
from ..utils.handler import FolderWatcher
from ..database.create import insert_single_task
from ..database.init import init_db

logger = get_logger(__name__)

class Uploader:
    def __init__(self):
        # self.watcher = FolderWatcher(OBSERVER_PATH) # 监控文件夹变化，将新文件信息放入队列
        
        self.api_id = API_ID
        self.api_hash = API_HASH
        self.clients = {}
        self.upload_scheduler = None
        
    async def create_clients(self):
        logger.info("开始加载 Telegram 客户端：发现 session 数量=%s", len(SESSION_FILES))
        for session_name, session_path in SESSION_FILES.items():
            client = TelegramClient(
                session_path,     # 直接传已有 session 的路径
                API_ID,
                API_HASH
            )
            await client.connect()
        
            # 检查是否已经授权（bot session 一般会是 True）
            if not await client.is_user_authorized():
                logger.warning("[%s] 未授权，需要重新登录：%s", session_name, session_path)
                continue
            
            me = await client.get_me()
            logger.info("[%s] Telegram 客户端加载成功：用户名=%s，用户ID=%s", session_name, me.username, me.id)
            self.clients[session_name] = client
        logger.info("Telegram 客户端加载完成：可用客户端数量=%s", len(self.clients))
 
    async def main(self):
        logger.info("Uploader 正在启动")
        await init_db()

        await self.create_clients()
        logger.info("所有客户端创建完成")
        
        self.upload_scheduler = UploadScheduler(workers={})
        
        workers = {}
        for name, client in self.clients.items():
            worker = BotWorker(name, client, self.upload_scheduler.notify)
            await worker.start()
            workers[name] = worker

        self.upload_scheduler.workers = workers


        logger.info("文件夹监听器已启动：监听目录=%s", self.upload_scheduler.watcher._path)

        await self.upload_scheduler.start()
    

        
