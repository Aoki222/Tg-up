import asyncio
from src.upload.uploader_application import UploaderApplication

if __name__ == "__main__":
    asyncio.run(UploaderApplication().run()) 