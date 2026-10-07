import os
import pytest
from src.database.connection import close_pool

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "ci_test_hash")

@pytest.fixture(autouse=True)
async def _close_db_pool_after_test():
    yield
    await close_pool()
