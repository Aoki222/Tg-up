from contextlib import AsyncExitStack
from pathlib import Path

from src.adapters.task_store import TaskRepository
from src.database.connection import POOL_SIZE, close_pool, get_db, open_pool
from src.database.init import SCHEMA


async def test_pool_reuses_connections(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    await open_pool(path)
    try:
        async with AsyncExitStack() as stack:
            seen = [id(await stack.enter_async_context(get_db())) for _ in range(POOL_SIZE)]
            assert len(set(seen)) == POOL_SIZE
        async with get_db() as again:
            assert id(again) in seen
    finally:
        await close_pool()


async def test_find_active_by_file_path_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    await open_pool(path)
    try:
        async with get_db() as database:
            await database.executescript(SCHEMA)
            await database.commit()
        repo = TaskRepository()
        file_path = str(tmp_path / "a.mp4")
        task_id = await repo.add_task(
            file_path=file_path,
            file_name="a.mp4",
            file_size=10,
            folder_name="d",
            chat_id=-100,
        )
        assert await repo.find_active_by_file_path(file_path) == task_id
        counts = await repo.count_active_by_workers()
        assert counts == {}
    finally:
        await close_pool()
