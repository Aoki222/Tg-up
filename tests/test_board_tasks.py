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


async def test_requeue_failed_resets_count_and_status(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        task_id = await repo.add_task(
            file_path=str(video),
            file_name="clip.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
            status="pending",
            max_retries=2,
        )
        await repo.mark_task_failed(task_id, 2, 2, "boom")
        assert await repo.requeue_failed(task_id) == "ok"
        row = await repo.get_task_by_id(task_id)
        assert row is not None
        assert row["status"] == "pending"
        assert int(row["retry_count"]) == 0
        assert row["error_msg"] == "manual retry"
        assert row["assigned_bot"] is None
    finally:
        await close_pool()


async def test_requeue_failed_rejects_non_failed_and_missing_file(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        pending_id = await repo.add_task(
            file_path=str(tmp_path / "p.mp4"),
            file_name="p.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
            status="pending",
        )
        assert await repo.requeue_failed(pending_id) == "not_failed"
        assert await repo.requeue_failed(99999) == "not_found"

        missing_id = await repo.add_task(
            file_path=str(tmp_path / "gone.mp4"),
            file_name="gone.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
            status="pending",
            max_retries=0,
        )
        await repo.mark_task_failed(missing_id, 0, 0, "x")
        assert await repo.requeue_failed(missing_id) == "missing_file"
    finally:
        await close_pool()


async def test_requeue_all_failed_skips_missing_files(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        video = tmp_path / "ok.mp4"
        video.write_bytes(b"x")
        ok_id = await repo.add_task(
            file_path=str(video),
            file_name="ok.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
            status="pending",
            max_retries=0,
        )
        gone_id = await repo.add_task(
            file_path=str(tmp_path / "gone.mp4"),
            file_name="gone.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
            status="pending",
            max_retries=0,
        )
        await repo.mark_task_failed(ok_id, 0, 0, "x")
        await repo.mark_task_failed(gone_id, 0, 0, "x")
        retried, skipped = await repo.requeue_all_failed()
        assert retried == 1
        assert skipped == 1
        ok_row = await repo.get_task_by_id(ok_id)
        gone_row = await repo.get_task_by_id(gone_id)
        assert ok_row is not None and ok_row["status"] == "pending"
        assert gone_row is not None and gone_row["status"] == "failed"
    finally:
        await close_pool()
