"""成功分页，以及过大列的批量切片、个人号和清除。"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.adapters.progress import ProgressHub
from src.api.app import OversizedIdsBody, create_api
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import SCHEMA
from src.domain.limits import OVERSIZED_REASON
from src.pipeline.slicer import SliceService


class _Hub:
    def get(self):
        return self

    def policy_for_row(self, row):
        from src.domain.task import AfterSuccess, TaskPolicy

        return TaskPolicy(need_preview=False, after_success=AfterSuccess.KEEP, max_retries=3)


class _After:
    async def handle(self, task) -> None:
        return None


class _Wake:
    def request_reschedule(self) -> None:
        return None


def _route(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(path)


async def _prepare(tmp_path: Path):
    await open_pool(tmp_path / "app.db")
    async with get_db() as database:
        await database.executescript(SCHEMA)
        await database.commit()
    from src.adapters.task_store import TaskRepository

    return TaskRepository()


async def test_success_page_skips_slice_parent_and_matches_today(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        normal = await repo.add_task(
            file_path=str(tmp_path / "ok.mp4"),
            file_name="ok.mp4",
            file_size=10,
            folder_name="films",
            chat_id=-100,
            status="pending",
        )
        parent = await repo.add_task(
            file_path=str(tmp_path / "huge.mp4"),
            file_name="huge.mp4",
            file_size=10,
            folder_name="films",
            chat_id=-100,
            status="oversized",
        )
        part = await repo.add_task(
            file_path=str(tmp_path / "part.mp4"),
            file_name="huge.part01-of-02.mp4",
            file_size=4,
            folder_name="films",
            chat_id=-100,
            status="pending",
        )
        async with get_db() as database:
            await database.execute(
                "UPDATE upload_tasks SET slice_parts = 2 WHERE id = ?",
                (parent,),
            )
            await database.execute(
                "UPDATE upload_tasks SET parent_id = ?, part_index = 1, part_count = 2 WHERE id = ?",
                (parent, part),
            )
            await database.commit()
        await repo.mark_task_succeeded(normal, 1)
        await repo.mark_task_succeeded(parent, 2)
        await repo.mark_task_succeeded(part, 3)

        items, total = await repo.list_success_page(scope="all", page=1, page_size=50)
        assert total == 2
        assert {row["file_name"] for row in items} == {"ok.mp4", "huge.part01-of-02.mp4"}
        today_items, today_total = await repo.list_success_page(scope="today", page=1, page_size=50)
        assert today_total == 2
        assert len(today_items) == 2
        counts = await repo.count_board_statuses()
        assert counts["success"] == today_total
        page_items, page_total = await repo.list_success_page(scope="all", page=2, page_size=1)
        assert page_total == 2
        assert len(page_items) == 1
        empty, still = await repo.list_success_page(scope="all", page=9, page_size=50)
        assert still == 2
        assert empty == []

        app = create_api(ProgressHub(), task_repository=repo)
        payload = await _route(app, "/api/tasks/success", "GET")(scope="all", page=1, page_size=50)
        assert payload["total"] == 2
        labels = {item["file_name"]: item["part_label"] for item in payload["items"]}
        assert labels["huge.part01-of-02.mp4"] == "第 1/2 段"
        assert labels["ok.mp4"] is None
    finally:
        await close_pool()


async def test_slice_batch_queues_video_and_skips_archive(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        video = tmp_path / "movie.mp4"
        video.write_bytes(b"v")
        archive = tmp_path / "disk.zip"
        archive.write_bytes(b"z")
        video_id = await repo.add_task(
            file_path=str(video),
            file_name="movie.mp4",
            file_size=2_100_000_000,
            folder_name="films",
            chat_id=-100,
            status="oversized",
            error_msg=OVERSIZED_REASON,
        )
        archive_id = await repo.add_task(
            file_path=str(archive),
            file_name="disk.zip",
            file_size=2_100_000_000,
            folder_name="films",
            chat_id=-100,
            status="oversized",
            error_msg=OVERSIZED_REASON,
        )
        service = SliceService(repo, _Hub(), _Wake(), _After(), tmp_path)
        app = create_api(ProgressHub(), task_repository=repo, slice_service=service)
        result = await _route(app, "/api/tasks/oversized/slice", "POST")(
            OversizedIdsBody(ids=[video_id, archive_id])
        )
        assert result["queued"] == 1
        assert result["skipped"][0]["id"] == archive_id
        assert result["skipped"][0]["reason"] == "这个文件不能切片"
        assert service.queue.qsize() == 1
    finally:
        await close_pool()


async def test_dispatch_batch_rejects_when_no_user(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        source = tmp_path / "movie.mp4"
        source.write_bytes(b"v")
        task_id = await repo.add_task(
            file_path=str(source),
            file_name="movie.mp4",
            file_size=2_100_000_000,
            folder_name="films",
            chat_id=-100,
            status="oversized",
            error_msg=OVERSIZED_REASON,
        )

        async def _no_user(_task_id: int) -> str:
            return "no_user"

        app = create_api(ProgressHub(), task_repository=repo, dispatch_oversized=_no_user)
        with pytest.raises(HTTPException) as caught:
            await _route(app, "/api/tasks/oversized/dispatch-user", "POST")(
                OversizedIdsBody(ids=[task_id])
            )
        assert caught.value.status_code == 409
        assert caught.value.detail == "没有可用的个人账号"
        row = await repo.get_task_by_id(task_id)
        assert row is not None and row["status"] == "oversized"
    finally:
        await close_pool()


async def test_oversized_delete_leaves_source_and_failed_delete_skips_it(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        source = tmp_path / "movie.mp4"
        source.write_bytes(b"source")
        huge_id = await repo.add_task(
            file_path=str(source),
            file_name="movie.mp4",
            file_size=2_100_000_000,
            folder_name="films",
            chat_id=-100,
            status="oversized",
            error_msg=OVERSIZED_REASON,
        )
        failed_id = await repo.add_task(
            file_path=str(tmp_path / "bad.mp4"),
            file_name="bad.mp4",
            file_size=3,
            folder_name="films",
            chat_id=-100,
            status="pending",
            max_retries=0,
        )
        await repo.mark_task_failed(failed_id, 0, 0, "x")
        assert await repo.delete_failed_ids([huge_id]) == 0
        assert await repo.get_task_by_id(huge_id) is not None

        service = SliceService(repo, _Hub(), _Wake(), _After(), tmp_path)
        app = create_api(ProgressHub(), task_repository=repo, slice_service=service)
        removed = await _route(app, "/api/tasks/oversized/delete", "POST")(
            OversizedIdsBody(ids=[huge_id])
        )
        assert removed["deleted"] == 1
        assert await repo.get_task_by_id(huge_id) is None
        assert source.is_file()
        assert await repo.get_task_by_id(failed_id) is not None
    finally:
        await close_pool()
