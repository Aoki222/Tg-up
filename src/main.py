import asyncio

from src.upload.core import Uploader


if __name__ == "__main__":
	asyncio.run(Uploader().main())