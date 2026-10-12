"""上传租约：超时回收之后，旧 Worker 不能把成功或失败写回去。"""

from pathlib import Path

from src.adapters.task_store import TaskRepository
from src.database.connection import close_pool, get_db
from src.database.init import SCHEMA


async def _prepare(tmp_path: Path) -> TaskRepository:
    from src.database.connection import open_pool

    await open_pool(tmp_path / "app.db")
    async with get_db() as database:
        await database.executescript(SCHEMA)
        await database.commit()
    return TaskRepository()


async def _add(repo: TaskRepository, tmp_path: Path, name: str, size: int) -> int:
    return await repo.add_task(
        file_path=str(tmp_path / name),
        file_name=name,
        file_size=size,
        folder_name="d",
        chat_id=-100,
        status="pending",
    )


async def test_stale_lease_cannot_overwrite_recovered_task(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        task_id = await _add(repo, tmp_path, "clip.mp4", 100)
        token = await repo.claim_task(task_id, "bot")
        assert token
        assert await repo.mark_task_uploading(task_id, claim_token=token) is True
        async with get_db() as database:
            await database.execute(
                "UPDATE upload_tasks SET started_at = datetime('now', '-3 hours') WHERE id = ?",
                (task_id,),
            )
            await database.commit()

        recovered = await repo.recover_timed_out_tasks(
            uploading_timeout_seconds=60,
            assigned_timeout_seconds=60,
        )
        assert task_id in recovered
        assert await repo.mark_task_succeeded(task_id, 99, claim_token=token) is False
        assert await repo.mark_task_failed(task_id, 3, 3, "late", claim_token=token) is False
        row = await repo.get_task_by_id(task_id)
        assert row is not None
        assert row["status"] == "pending"
        assert row["claim_token"] is None

        fresh = await repo.claim_task(task_id, "bot_2")
        assert fresh and fresh != token
        assert await repo.mark_task_uploading(task_id, claim_token=token) is False
        assert await repo.mark_task_uploading(task_id, claim_token=fresh) is True
        assert await repo.mark_task_succeeded(task_id, 7, claim_token=fresh) is True
        done = await repo.get_task_by_id(task_id)
        assert done is not None
        assert done["status"] == "success"
        assert done["claim_token"] is None
    finally:
        await close_pool()


async def test_success_without_token_still_writes(tmp_path: Path) -> None:
    """切片父任务不在 uploading，完成时仍按无租约路径记账。"""
    try:
        repo = await _prepare(tmp_path)
        task_id = await repo.add_task(
            file_path=str(tmp_path / "parent.mp4"),
            file_name="parent.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="oversized",
        )
        assert await repo.mark_task_succeeded(task_id, 5) is True
        row = await repo.get_task_by_id(task_id)
        assert row is not None and row["status"] == "success"
    finally:
        await close_pool()


async def test_pending_order_smallest_or_oldest(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        large = await _add(repo, tmp_path, "large.mp4", 500)
        small = await _add(repo, tmp_path, "small.mp4", 5)
        oldest = await repo.fetch_pending_tasks(10, order="oldest")
        smallest = await repo.fetch_pending_tasks(10, order="smallest")
        assert [int(row["id"]) for row in oldest] == [large, small]
        assert [int(row["id"]) for row in smallest] == [small, large]
    finally:
        await close_pool()
