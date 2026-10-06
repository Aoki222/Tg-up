"""超过 Bot 上限的文件：入库、停在 oversized、只交给个人号。"""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from src.adapters.task_store import TaskRepository
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import (
    SCHEMA,
    _allow_oversized_status,
    _ensure_upload_task_indexes,
    _fresh_upload_tasks_sql,
    _park_existing_oversized,
    init_db,
)
from src.domain.limits import OVERSIZED_REASON, PARTS_INVALID_REASON
from src.domain.task import AfterSuccess, Task, TaskArtifacts, TaskDestination, TaskPolicy, TaskStatus
from src.pipeline.application import UploaderApplication
from src.pipeline.worker import UploadWorker
from src.ports.transport import SendOversized


async def _prepare(tmp_path: Path) -> TaskRepository:
    await open_pool(tmp_path / "app.db")
    async with get_db() as database:
        await database.executescript(SCHEMA)
        await database.commit()
    return TaskRepository()


def _task(path: Path, size: int) -> Task:
    return Task(
        id=1,
        file_path=str(path),
        file_name=path.name,
        file_size=size,
        destination=TaskDestination(chat_id=-100, dest_id="-100", platform="telegram"),
        artifacts=TaskArtifacts(video_path=str(path)),
        policy=TaskPolicy(need_preview=False, after_success=AfterSuccess.KEEP, max_retries=3),
        status=TaskStatus.ASSIGNED,
    )


class _Hub:
    def get(self):
        return SimpleNamespace(concurrency=1, upload_timeout_seconds=30)

    def policy_for_row(self, row: dict) -> TaskPolicy:
        return TaskPolicy(need_preview=False, after_success=AfterSuccess.KEEP, max_retries=3)


class _Repo:
    def __init__(self) -> None:
        self.parked: tuple[int, str] | None = None
        self.uploads = 0

    async def get_task_by_id(self, task_id: int):
        return None

    async def park_oversized(self, task_id: int, error_message: str) -> None:
        self.parked = (task_id, error_message)

    async def mark_task_uploading(self, task_id: int) -> None:
        self.uploads += 1


class _Transport:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0

    async def send(self, task, timeout_seconds: int, on_progress=None):
        self.calls += 1
        return self.result


class _Worker:
    def __init__(self, name: str, kind: str, accepting: bool = True) -> None:
        self.worker_name = name
        self.account_kind = kind
        self.accepting = accepting
        self.queued: list[Task] = []

    def is_accepting(self) -> bool:
        return self.accepting

    async def enqueue_task(self, task: Task) -> None:
        self.queued.append(task)


async def _drive(worker: UploadWorker, task: Task) -> None:
    await worker.task_queue.put(task)
    await worker.task_queue.get()
    await worker.process_single_task(task)


async def test_bot_parks_oversized_without_uploading(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.domain.limits.TELEGRAM_BOT_MAX_BYTES", 1)
    repo = _Repo()
    transport = _Transport(SendOversized("should-not-send"))
    worker = UploadWorker("homa", repo, transport, SimpleNamespace(), _Hub(), account_kind="bot")
    await _drive(worker, _task(tmp_path / "big.mp4", 5))
    assert transport.calls == 0
    assert repo.uploads == 0
    assert repo.parked == (1, OVERSIZED_REASON)


async def test_user_upload_that_exceeds_parts_returns_to_oversized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.domain.limits.TELEGRAM_BOT_MAX_BYTES", 1)
    repo = _Repo()
    transport = _Transport(SendOversized(PARTS_INVALID_REASON))
    worker = UploadWorker("user_1", repo, transport, SimpleNamespace(), _Hub(), account_kind="user")
    await _drive(worker, _task(tmp_path / "big.mp4", 5))
    assert transport.calls == 1
    assert repo.uploads == 1
    assert repo.parked == (1, PARTS_INVALID_REASON)


async def test_release_failure_and_preview_keep_huge_files_out_of_pending(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.domain.limits.TELEGRAM_BOT_MAX_BYTES", 10)
    try:
        repo = await _prepare(tmp_path)
        huge = await repo.add_task(
            file_path=str(tmp_path / "huge.mp4"),
            file_name="huge.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="pending",
        )
        await repo.claim_task(huge, "bot")
        await repo.release_task(huge, "worker disabled")
        released = await repo.get_task_by_id(huge)
        assert released is not None
        assert released["status"] == "oversized"

        preparing = await repo.add_task(
            file_path=str(tmp_path / "cover.mp4"),
            file_name="cover.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="preparing",
        )
        await repo.update_preview(preparing, None, True)
        covered = await repo.get_task_by_id(preparing)
        assert covered is not None
        assert covered["status"] == "oversized"
        assert covered["error_msg"] == OVERSIZED_REASON

        failed = await repo.add_task(
            file_path=str(tmp_path / "retry.mp4"),
            file_name="retry.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="pending",
            max_retries=3,
        )
        await repo.mark_task_failed(failed, 1, 3, "boom")
        parked = await repo.get_task_by_id(failed)
        assert parked is not None
        assert parked["status"] == "oversized"

        assert await repo.fetch_pending_tasks(10) == []
        rows = await repo.list_board_tasks()
        assert {int(row["id"]) for row in rows} >= {huge, preparing, failed}
        assert (await repo.count_board_statuses())["oversized"] == 3
    finally:
        await close_pool()


async def test_requeue_of_huge_failed_file_does_not_return_to_bots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.domain.limits.TELEGRAM_BOT_MAX_BYTES", 10)
    try:
        repo = await _prepare(tmp_path)
        video = tmp_path / "huge.mp4"
        video.write_bytes(b"x")
        missing = tmp_path / "gone.mp4"
        small = tmp_path / "small.mp4"
        small.write_bytes(b"x")
        huge_again = tmp_path / "huge-again.mp4"
        huge_again.write_bytes(b"x")
        huge_id = await repo.add_task(
            file_path=str(video),
            file_name="huge.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="failed",
        )
        small_id = await repo.add_task(
            file_path=str(small),
            file_name="small.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
            status="failed",
        )
        gone_id = await repo.add_task(
            file_path=str(missing),
            file_name="gone.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="failed",
        )
        again_id = await repo.add_task(
            file_path=str(huge_again),
            file_name="huge-again.mp4",
            file_size=80,
            folder_name="d",
            chat_id=-100,
            status="failed",
        )
        assert await repo.requeue_failed(huge_id) == "ok"
        huge_row = await repo.get_task_by_id(huge_id)
        assert huge_row is not None and huge_row["status"] == "oversized"
        retried, skipped, parked = await repo.requeue_all_failed()
        assert retried == 1
        assert skipped == 1
        assert parked == 1
        again_row = await repo.get_task_by_id(again_id)
        assert again_row is not None and again_row["status"] == "oversized"
        small_row = await repo.get_task_by_id(small_id)
        gone_row = await repo.get_task_by_id(gone_id)
        assert small_row is not None and small_row["status"] == "pending"
        assert gone_row is not None and gone_row["status"] == "failed"
        assert await repo.delete_failed(huge_id) == "ok"
        assert await repo.get_task_by_id(huge_id) is None
    finally:
        await close_pool()


def _upload_tasks_before_slice_columns() -> str:
    """切片功能之前的 upload_tasks：没有 parent_id / part_* / slice_parts。"""
    statement = _fresh_upload_tasks_sql()
    dropped = ("parent_id", "part_index", "part_count", "slice_parts")
    lines = [line for line in statement.splitlines() if not any(name in line for name in dropped)]
    return "\n".join(lines)


async def _columns_and_index() -> tuple[set[str], bool, str | None]:
    async with get_db() as database:
        async with database.execute("PRAGMA table_info(upload_tasks)") as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        async with database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_upload_tasks_parent'"
        ) as cursor:
            has_index = await cursor.fetchone() is not None
        async with database.execute(
            "SELECT status FROM upload_tasks WHERE task_id = 'keep-me'"
        ) as cursor:
            row = await cursor.fetchone()
    status = None if row is None else str(row["status"])
    return columns, has_index, status


async def test_legacy_database_without_slice_columns_migrates(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(_upload_tasks_before_slice_columns())
        connection.execute(
            """INSERT INTO upload_tasks (task_id, file_path, file_name, file_size, chat_id, status)
               VALUES ('keep-me', 'a.mp4', 'a.mp4', 3, -100, 'pending')"""
        )
    monkeypatch.setattr("src.database.connection.DATABASE_PATH", db_path)
    try:
        await init_db()
        columns, has_index, status = await _columns_and_index()
        assert {"parent_id", "part_index", "part_count", "slice_parts"} <= columns
        assert has_index
        assert status == "pending"
        async with get_db() as database:
            async with database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'upload_slices'"
            ) as cursor:
                assert await cursor.fetchone() is not None
    finally:
        await close_pool()


async def test_fresh_database_creates_parent_index(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr("src.database.connection.DATABASE_PATH", db_path)
    try:
        await init_db()
        columns, has_index, status = await _columns_and_index()
        assert "parent_id" in columns
        assert has_index
        assert status is None
    finally:
        await close_pool()


async def test_old_check_constraint_is_rebuilt_and_huge_rows_park(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.domain.limits.TELEGRAM_BOT_MAX_BYTES", 10)
    old_schema = SCHEMA.replace(",'oversized'", "").replace("oversized", "hold")
    try:
        await open_pool(tmp_path / "legacy.db")
        async with get_db() as database:
            await database.executescript(old_schema)
            await database.execute(
                """INSERT INTO upload_tasks (task_id, file_path, file_name, file_size, chat_id, status)
                   VALUES ('a', 'a.mp4', 'a.mp4', 100, -100, 'pending')"""
            )
            await database.execute(
                """INSERT INTO upload_tasks (task_id, file_path, file_name, file_size, chat_id, status)
                   VALUES ('b', 'b.mp4', 'b.mp4', 1, -100, 'pending')"""
            )
            await database.commit()
            await _allow_oversized_status(database)
            await _ensure_upload_task_indexes(database)
            parked = await _park_existing_oversized(database)
            await database.commit()
        assert parked == 1
        repo = TaskRepository()
        rows = await repo.fetch_pending_tasks(10)
        assert [int(row["file_size"]) for row in rows] == [1]
        huge = await repo.get_task_by_id(1)
        assert huge is not None and huge["status"] == "oversized"
        assert huge["error_msg"] == OVERSIZED_REASON
        fresh = await repo.add_task(
            file_path=str(tmp_path / "c.mp4"),
            file_name="c.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
        )
        assert fresh == 3
        async with get_db() as database:
            async with database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'upload_tasks'"
            ) as cursor:
                names = {str(row[0]) for row in await cursor.fetchall()}
        assert "idx_status" in names
        assert "idx_status_platform" in names
    finally:
        await close_pool()


async def test_dispatch_picks_the_least_loaded_user(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        video = tmp_path / "huge.mp4"
        video.write_bytes(b"x")
        small = tmp_path / "small.mp4"
        small.write_bytes(b"x")
        busy_id = await repo.add_task(
            file_path=str(small),
            file_name="small.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
            status="pending",
        )
        await repo.claim_task(busy_id, "user_a")
        huge_id = await repo.add_task(
            file_path=str(video),
            file_name="huge.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="oversized",
            error_msg=OVERSIZED_REASON,
        )
        user_a = _Worker("user_a", "user")
        user_b = _Worker("user_b", "user")
        bot = _Worker("homa", "bot")
        unknown = _Worker("alice", "unknown")
        offline = _Worker("user_c", "user", accepting=False)
        app = UploaderApplication()
        app._repository = repo
        app._settings_hub = _Hub()
        app._scheduler = SimpleNamespace(
            worker_map={
                "user_a": user_a,
                "user_b": user_b,
                "homa": bot,
                "alice": unknown,
                "user_c": offline,
            }
        )
        assert await app.dispatch_oversized_to_user(huge_id) == "ok"
        assert user_a.queued == []
        assert len(user_b.queued) == 1
        assert user_b.queued[0].id == huge_id
        assert bot.queued == []
        assert await app.dispatch_oversized_to_user(huge_id) == "not_oversized"
        assert await app.dispatch_oversized_to_user(999) == "not_found"
        pending_id = await repo.add_task(
            file_path=str(tmp_path / "p.mp4"),
            file_name="p.mp4",
            file_size=1,
            folder_name="d",
            chat_id=-100,
            status="pending",
        )
        assert await app.dispatch_oversized_to_user(pending_id) == "not_oversized"
        missing_id = await repo.add_task(
            file_path=str(tmp_path / "gone.mp4"),
            file_name="gone.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="oversized",
        )
        assert await app.dispatch_oversized_to_user(missing_id) == "missing_file"
        app._scheduler.worker_map = {"homa": bot, "alice": unknown, "user_c": offline}
        lonely = await repo.add_task(
            file_path=str(video),
            file_name="huge.mp4",
            file_size=100,
            folder_name="d",
            chat_id=-100,
            status="oversized",
        )
        assert await app.dispatch_oversized_to_user(lonely) == "no_user"
        row = await repo.get_task_by_id(lonely)
        assert row is not None and row["status"] == "oversized"
    finally:
        await close_pool()
