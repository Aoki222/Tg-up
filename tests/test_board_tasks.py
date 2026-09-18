from pathlib import Path

from src.adapters.progress import ProgressHub
from src.adapters.task_store import TaskRepository
from src.api.app import _board_item
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import SCHEMA
from src.domain.progress import make_progress


async def _prepare(tmp_path: Path) -> TaskRepository:
    await open_pool(tmp_path / "app.db")
    async with get_db() as database:
        await database.executescript(SCHEMA)
        await database.commit()
    return TaskRepository()


async def test_list_board_tasks_excludes_success(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        preparing = await repo.add_task(
            file_path=str(tmp_path / "p.mp4"),
            file_name="p.mp4",
            file_size=10,
            folder_name="d",
            chat_id=-100,
            status="preparing",
        )
        pending = await repo.add_task(
            file_path=str(tmp_path / "q.mp4"),
            file_name="q.mp4",
            file_size=20,
            folder_name="d",
            chat_id=-100,
            status="pending",
        )
        done = await repo.add_task(
            file_path=str(tmp_path / "s.mp4"),
            file_name="s.mp4",
            file_size=30,
            folder_name="d",
            chat_id=-100,
            status="pending",
        )
        await repo.claim_task(done, "bot")
        await repo.mark_task_uploading(done)
        await repo.mark_task_succeeded(done, 1)

        failed = await repo.add_task(
            file_path=str(tmp_path / "f.mp4"),
            file_name="f.mp4",
            file_size=40,
            folder_name="d",
            chat_id=-100,
            status="pending",
            max_retries=1,
        )
        await repo.mark_task_failed(failed, 1, 1, "boom")

        rows = await repo.list_board_tasks()
        ids = {int(row["id"]) for row in rows}
        assert preparing in ids
        assert pending in ids
        assert failed in ids
        assert done not in ids

        counts = await repo.count_board_statuses()
        assert counts["preparing"] == 1
        assert counts["pending"] == 1
        assert counts["failed"] == 1
        assert counts["uploading"] == 0
        assert counts["assigned"] == 0
    finally:
        await close_pool()


async def test_failed_board_is_capped(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        for i in range(5):
            task_id = await repo.add_task(
                file_path=str(tmp_path / f"{i}.mp4"),
                file_name=f"{i}.mp4",
                file_size=i + 1,
                folder_name="d",
                chat_id=-100,
                status="pending",
                max_retries=0,
            )
            await repo.mark_task_failed(task_id, 0, 0, "x")
        rows = await repo.list_board_tasks(failed_limit=3)
        assert len(rows) == 3
        assert await repo.count_board_statuses() == {
            "preparing": 0,
            "pending": 0,
            "assigned": 0,
            "uploading": 0,
            "failed": 5,
        }
    finally:
        await close_pool()


async def test_tasks_endpoint_overlays_speed(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        task_id = await repo.add_task(
            file_path=str(tmp_path / "a.mp4"),
            file_name="a.mp4",
            file_size=1_000_000,
            folder_name="d",
            chat_id=-100,
            status="pending",
        )
        async with get_db() as database:
            await database.execute(
                "UPDATE upload_tasks SET status = 'uploading', assigned_bot = ? WHERE id = ?",
                ("bot", task_id),
            )
            await database.commit()

        hub = ProgressHub()
        hub.report(
            make_progress(
                task_id=task_id,
                worker_name="bot",
                file_name="a.mp4",
                current=250_000,
                total=1_000_000,
                stage="uploading",
            )
        )
        rows = await repo.list_board_tasks()
        counts = await repo.count_board_statuses()
        latest = {item.task_id: item for item in hub.snapshot()}
        items = [_board_item(row, latest.get(int(row["id"]))) for row in rows]
        assert counts["uploading"] == 1
        item = items[0]
        assert item["id"] == task_id
        assert item["status"] == "uploading"
        assert item["percent"] == 25.0
        assert item["current"] == 250_000
        assert item["assigned_worker"] == "bot"
    finally:
        await close_pool()
