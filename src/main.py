"""进程入口。在项目根目录执行: python -m src.main

会同时启动目录监听流水线和 http://127.0.0.1:8000/ 控制台。
"""

import asyncio

from src.pipeline.application import UploaderApplication

if __name__ == "__main__":
    try:
        asyncio.run(UploaderApplication().run())
    except KeyboardInterrupt:
        pass

