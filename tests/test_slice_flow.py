"""切片队列：分段入库、逐段上传、源文件按原策略收尾。ffmpeg 被替掉。"""

from pathlib import Path

from src.adapters.task_store import TaskRepository
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import SCHEMA
from src.domain.limits import OVERSIZED_REASON
from src.domain.slices import MSG_INTERRUPTED, MSG_WAIT, msg_need_parts, msg_part, msg_part_failed
from src.domain.task import AfterSuccess, Task, TaskArtifacts, TaskDestination, TaskPolicy, TaskStatus
from src.pipeline.slice_media import SliceCutError
from src.pipeline.slicer import SliceService

HUGE = 2_100_000_000


class _Hub:
    def __init__(self, auto_slice: bool = False):
        self.auto_slice = auto_slice
        self.handled: list[str] = []

    def get(self):
        return self

    def policy_for_row(self, row: dict) -> TaskPolicy:
        raw = str(row.get("after_success") or "keep")
        return TaskPolicy(need_preview=False, after_success=AfterSuccess(raw), max_retries=3)


class _After:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def handle(self, task: Task) -> None:
        self.paths.append(task.file_path)


class _Wake:
    def request_reschedule(self) -> None:
        return None


async def _prepare(tmp_path: Path) -> TaskRepository:
    await open_pool(tmp_path / "app.db")
    async with get_db() as database:
        await database.executescript(SCHEMA)
        await database.commit()
    return TaskRepository()


def _service(tmp_path: Path, repo: TaskRepository, hub: _Hub, after: _After) -> SliceService:
    return SliceService(repo, hub, _Wake(), after, tmp_path)


async def _parent(repo: TaskRepository, tmp_path: Path, name: str = "movie.mp4") -> tuple[int, Path]:
    source = tmp_path / name
    source.write_bytes(b"source")
    task_id = await repo.add_task(
        file_path=str(source),
        file_name=name,
        file_size=HUGE,
        folder_name="films",
        chat_id=-100,
        status="oversized",
        caption="原说明",
        error_msg=OVERSIZED_REASON,
        after_success="keep",
    )
    return task_id, source


def _child_task(row: dict) -> Task:
    return Task(
        id=int(row["id"]),
        file_path=row["file_path"],
        file_name=row["file_name"],
        file_size=int(row["file_size"]),
        destination=TaskDestination(chat_id=-100, dest_id="-100"),
        artifacts=TaskArtifacts(video_path=row["file_path"]),
        policy=TaskPolicy(need_preview=False, after_success=AfterSuccess.KEEP, max_retries=3),
        status=TaskStatus.SUCCESS,
        parent_id=int(row["parent_id"]),
        part_index=int(row["part_index"]),
        part_count=int(row["part_count"]),
    )


async def test_parts_upload_one_by_one_and_source_stays(tmp_path: Path, monkeypatch) -> None:
    try:
        repo = await _prepare(tmp_path)
        parent_id, source = await _parent(repo, tmp_path)
        after = _After()
        service = _service(tmp_path, repo, _Hub(), after)

        async def fake_cut(source_path: Path, directory: Path, parts: int):
            made = []
            for index in range(1, parts + 1):
                path = directory / f"movie.part0{index}-of-0{parts}.mp4"
                path.write_bytes(b"part")
                made.append((path, 4))
            return made

        monkeypatch.setattr("src.pipeline.slicer.cut_into_parts", fake_cut)
        assert await service.request(parent_id, 2) == "ok"
        assert await service.request(parent_id, 2) == "busy"
        await service._slice_parent(parent_id)

        parent = await repo.get_task_by_id(parent_id)
        assert parent is not None
        assert parent["error_msg"] == msg_part(1, 2)
        assert parent["status"] == "oversized"
        slices = await repo.list_slices(parent_id)
        assert [row["status"] for row in slices] == ["queued", "planned"]
        board = await repo.list_board_tasks()
        assert [int(row["id"]) for row in board] == [parent_id]

        first = await repo.get_task_by_id(int(slices[0]["child_task_id"]))
        assert first is not None
        assert first["caption"] == "原说明\n（1/2）"
        assert first["after_success"] == "keep"
        await repo.mark_task_succeeded(int(first["id"]), 11)
        await service.on_child(_child_task(first), "success")
        assert not Path(first["file_path"]).exists()

        parent = await repo.get_task_by_id(parent_id)
        assert parent is not None and parent["error_msg"] == msg_part(2, 2)
        second_row = await repo.list_slices(parent_id)
        second = await repo.get_task_by_id(int(second_row[1]["child_task_id"]))
        assert second is not None and second["status"] == "pending"
        await repo.mark_task_succeeded(int(second["id"]), 22)
        await service.on_child(_child_task(second), "success")

        parent = await repo.get_task_by_id(parent_id)
        assert parent is not None
        assert parent["status"] == "success"
        assert parent["remote_id"] == "22"
        assert after.paths == [str(source)]
        assert source.is_file()
        assert not service.slice_dir(parent_id).exists()
    finally:
        await close_pool()


async def test_oversize_segment_keeps_source_and_reports_minimum(tmp_path: Path, monkeypatch) -> None:
    try:
        repo = await _prepare(tmp_path)
        parent_id, source = await _parent(repo, tmp_path)
        service = _service(tmp_path, repo, _Hub(), _After())

        async def fake_cut(source_path: Path, directory: Path, parts: int):
            raise SliceCutError(msg_need_parts(4))

        monkeypatch.setattr("src.pipeline.slicer.cut_into_parts", fake_cut)
        assert await service.request(parent_id, 2) == "ok"
        await service._slice_parent(parent_id)
        parent = await repo.get_task_by_id(parent_id)
        assert parent is not None
        assert parent["status"] == "oversized"
        assert parent["error_msg"] == msg_need_parts(4)
        assert parent["slice_parts"] is None
        assert source.is_file()
        assert await service.request(parent_id, 1) == "bad_parts:2"
    finally:
        await close_pool()


async def test_failed_part_can_continue_without_cutting_again(tmp_path: Path, monkeypatch) -> None:
    try:
        repo = await _prepare(tmp_path)
        parent_id, _source = await _parent(repo, tmp_path)
        service = _service(tmp_path, repo, _Hub(), _After())

        async def fake_cut_both(source_path: Path, directory: Path, parts: int):
            made = []
            for index in range(1, parts + 1):
                path = directory / f"movie.part0{index}-of-0{parts}.mp4"
                path.write_bytes(b"part")
                made.append((path, 4))
            return made

        monkeypatch.setattr("src.pipeline.slicer.cut_into_parts", fake_cut_both)
        assert await service.request(parent_id, 2) == "ok"
        await service._slice_parent(parent_id)
        slices = await repo.list_slices(parent_id)
        child = await repo.get_task_by_id(int(slices[0]["child_task_id"]))
        assert child is not None
        await repo.mark_task_failed(int(child["id"]), 3, 3, "上传失败")
        await service.on_child(_child_task(child), "failed")
        parent = await repo.get_task_by_id(parent_id)
        assert parent is not None and parent["error_msg"] == msg_part_failed(1, 2)
        assert await repo.claim_oversized(parent_id, "user_1") is False
        assert await service.continue_upload(parent_id) == "ok"
        retried = await repo.get_task_by_id(int(child["id"]))
        assert retried is not None and retried["status"] == "pending"
        assert Path(child["file_path"]).is_file()
    finally:
        await close_pool()


async def test_auto_slice_only_for_new_videos_when_enabled(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        video_id, _video = await _parent(repo, tmp_path, "movie.mp4")
        archive = tmp_path / "disk.zip"
        archive.write_bytes(b"zip")
        archive_id = await repo.add_task(
            file_path=str(archive),
            file_name="disk.zip",
            file_size=HUGE,
            folder_name="films",
            chat_id=-100,
            status="oversized",
            error_msg=OVERSIZED_REASON,
        )
        off = _service(tmp_path, repo, _Hub(False), _After())
        await off.offer_auto(video_id)
        stayed = await repo.get_task_by_id(video_id)
        assert stayed is not None and stayed["error_msg"] == OVERSIZED_REASON

        on = _service(tmp_path, repo, _Hub(True), _After())
        await on.offer_auto(archive_id)
        packed = await repo.get_task_by_id(archive_id)
        assert packed is not None and packed["error_msg"] == OVERSIZED_REASON
        await on.offer_auto(video_id)
        queued = await repo.get_task_by_id(video_id)
        assert queued is not None and queued["error_msg"] == MSG_WAIT
        assert on.queue.qsize() == 1
    finally:
        await close_pool()


async def test_interrupted_cut_is_not_restarted(tmp_path: Path) -> None:
    try:
        repo = await _prepare(tmp_path)
        parent_id, source = await _parent(repo, tmp_path)
        service = _service(tmp_path, repo, _Hub(), _After())
        await repo.begin_slice(parent_id, 2, "正在切片")
        junk = service.slice_dir(parent_id)
        junk.mkdir(parents=True)
        (junk / "half.mp4").write_bytes(b"half")
        await service.recover()
        parent = await repo.get_task_by_id(parent_id)
        assert parent is not None
        assert parent["error_msg"] == MSG_INTERRUPTED
        assert parent["slice_parts"] is None
        assert not junk.exists()
        assert source.is_file()
        assert service.queue.qsize() == 0
    finally:
        await close_pool()


async def test_clear_removes_segments_and_leaves_source(tmp_path: Path, monkeypatch) -> None:
    try:
        repo = await _prepare(tmp_path)
        parent_id, source = await _parent(repo, tmp_path)
        service = _service(tmp_path, repo, _Hub(), _After())

        async def fake_cut(source_path: Path, directory: Path, parts: int):
            path = directory / "movie.part01-of-02.mp4"
            path.write_bytes(b"part")
            second = directory / "movie.part02-of-02.mp4"
            second.write_bytes(b"part")
            return [(path, 4), (second, 4)]

        monkeypatch.setattr("src.pipeline.slicer.cut_into_parts", fake_cut)
        assert await service.request(parent_id, 2) == "ok"
        await service._slice_parent(parent_id)
        await service.discard(parent_id)
        assert await repo.delete_failed(parent_id) == "ok"
        assert await repo.get_task_by_id(parent_id) is None
        assert source.is_file()
        assert not service.slice_dir(parent_id).exists()
        assert await repo.list_slices(parent_id) == []
    finally:
        await close_pool()
