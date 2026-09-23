from pathlib import Path

from src.adapters.task_store import TaskRepository
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import SCHEMA, _backfill_destination_columns
from src.domain.task import AfterSuccess, TaskPolicy, task_from_row


async def _prepare(tmp_path: Path) -> TaskRepository:
    await open_pool(tmp_path / "app.db")
    async with get_db() as database:
        await database.executescript(SCHEMA)
        await database.commit()
    return TaskRepository()


async def test_add_task_dual_writes_destination(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        task_id = await repo.add_task(
            file_path=str(tmp_path / "a.mp4"),
            file_name="a.mp4",
            file_size=3,
            folder_name="d",
            chat_id=-100,
            topic_id=7,
        )
        row = await repo.get_task_by_id(task_id)
        assert row is not None
        assert row["platform"] == "telegram"
        assert row["dest_id"] == "-100"
        assert row["dest_extra"] == "7"
        assert row["chat_id"] == -100
        assert row["topic_id"] is None
        task = task_from_row(
            row,
            TaskPolicy(need_preview=False, after_success=AfterSuccess.KEEP, max_retries=3),
        )
        assert task.destination.platform == "telegram"
        assert task.destination.dest_id == "-100"
        assert task.destination.chat_id == -100
        assert task.destination.topic_id == 7
    finally:
        await close_pool()


async def test_backfill_legacy_row_and_claim_writes_both_workers(tmp_path: Path) -> None:
    try:
        await open_pool(tmp_path / "app.db")
        async with get_db() as database:
            await database.executescript(SCHEMA)
            await database.execute(
                """INSERT INTO upload_tasks
                   (task_id, file_path, file_name, file_size, chat_id, topic_id, status, telegram_msg_id, assigned_bot)
                   VALUES ('legacy', 'c.mp4', 'c.mp4', 1, -200, 3, 'pending', '99', NULL)"""
            )
            await database.execute(
                """UPDATE upload_tasks SET dest_id = '', dest_extra = NULL, remote_id = NULL
                   WHERE task_id = 'legacy'"""
            )
            await _backfill_destination_columns(database)
            await database.commit()
        repo = TaskRepository()
        row = await repo.get_task_by_id(1)
        assert row is not None
        assert row["platform"] == "telegram"
        assert row["dest_id"] == "-200"
        assert row["dest_extra"] == "3"
        assert await repo.claim_task(1, "bot_a")
        claimed = await repo.get_task_by_id(1)
        assert claimed is not None
        assert claimed["assigned_bot"] is None
        assert claimed["assigned_worker"] == "bot_a"
        await repo.mark_task_uploading(1)
        await repo.mark_task_succeeded(1, 99)
        done = await repo.get_task_by_id(1)
        assert done is not None
        assert done["telegram_msg_id"] == "99" or done["telegram_msg_id"] == 99
        assert str(done["remote_id"]) == "99"
    finally:
        await close_pool()
