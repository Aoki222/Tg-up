"""进程入口。在项目根目录执行: python -m src.main"""

import asyncio

from src.pipeline.application import UploaderApplication

if __name__ == "__main__":
    asyncio.run(UploaderApplication().run())

