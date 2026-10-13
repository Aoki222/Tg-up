from pathlib import Path

from src.adapters.task_store import TaskRepository
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import SCHEMA


async def _repo(tmp_path: Path) -> TaskRepository:
    await open_pool(tmp_path / "app.db")
    async with get_db() as database:
        await database.executescript(SCHEMA)
        await database.commit()
    return TaskRepository()


async def _status(task_id: int, status: str) -> None:
    async with get_db() as database:
        await database.execute(
            "UPDATE upload_tasks SET status = ? WHERE id = ?",
            (status, task_id),
        )
        await database.commit()


async def _add(repo: TaskRepository, path: str, size: int, mtime: float | None, after_success: str = "keep") -> int:
    return await repo.add_task(
        file_path=path,
        file_name="a.mp4",
        file_size=size,
        folder_name="d",
        chat_id=-100,
        after_success=after_success,
        source_mtime=mtime,
    )


async def test_pending_blocks_even_when_the_size_differs(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    try:
        path = str(tmp_path / "a.mp4")
        task_id = await _add(repo, path, 10, 100.0)
        assert await repo.find_ingest_block(path, 99, 1.0) == ("active", task_id)
    finally:
        await close_pool()


async def test_keep_success_matches_size_and_mtime(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    try:
        path = str(tmp_path / "a.mp4")
        task_id = await _add(repo, path, 10, 100.0)
        await _status(task_id, "success")
        assert await repo.find_ingest_block(path, 10, 100.0) == ("same", task_id)
        assert await repo.find_ingest_block(path, 10, 100.009) == ("same", task_id)
        assert await repo.find_ingest_block(path, 10, 100.02) is None
        assert await repo.find_ingest_block(path, 11, 100.0) is None
    finally:
        await close_pool()


async def test_archive_success_uses_the_same_fingerprint_as_keep(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    try:
        path = str(tmp_path / "a.mp4")
        task_id = await _add(repo, path, 10, 100.0, after_success="move_to_archive")
        await _status(task_id, "success")
        assert await repo.find_ingest_block(path, 10, 100.0) == ("same", task_id)
        assert await repo.find_ingest_block(path, 10, 200.0) is None
    finally:
        await close_pool()


async def test_delete_success_ignores_mtime_when_size_matches(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    try:
        path = str(tmp_path / "a.mp4")
        task_id = await _add(repo, path, 10, 100.0, after_success="delete")
        await _status(task_id, "success")
        assert await repo.find_ingest_block(path, 10, 999.0) == ("same", task_id)
        assert await repo.find_ingest_block(path, 11, 100.0) is None
    finally:
        await close_pool()


async def test_legacy_row_without_mtime_matches_size_only(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    try:
        path = str(tmp_path / "a.mp4")
        task_id = await _add(repo, path, 10, None)
        await _status(task_id, "success")
        assert await repo.find_ingest_block(path, 10, 50.0) == ("same", task_id)
        assert await repo.find_ingest_block(path, 9, 50.0) is None
    finally:
        await close_pool()


async def test_failed_row_keeps_mtime_even_if_delete_was_configured(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    try:
        path = str(tmp_path / "a.mp4")
        task_id = await _add(repo, path, 10, 100.0, after_success="delete")
        await _status(task_id, "failed")
        assert await repo.find_ingest_block(path, 10, 100.0) == ("same", task_id)
        assert await repo.find_ingest_block(path, 10, 100.05) is None
    finally:
        await close_pool()
