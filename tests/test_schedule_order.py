"""上传顺序：入库顺序、自然排序、创建时间、文件大小。排序在 SQL 里完成。"""

from pathlib import Path

from src.adapters.task_store import TaskRepository
from src.database.connection import close_pool, get_db, open_pool
from src.database.init import SCHEMA, _backfill_schedule_keys
from src.domain.schedule_key import child_natural_key, make_natural_key
from src.domain.settings_hub import SettingsHub, render_upload_toml


def test_natural_key_orders_episode_numbers_and_directories() -> None:
    ep2 = make_natural_key(r"D:\show\Season 2\EP2.mkv")
    ep10 = make_natural_key(r"D:\show\Season 2\EP10.mkv")
    ep1 = make_natural_key(r"D:\show\Season 2\ep1.mkv")
    season10 = make_natural_key(r"D:\show\Season 10\EP1.mkv")
    assert ep1 < ep2 < ep10
    assert ep10 < season10
    assert f"{2:018d}" in ep2
    assert f"{10:018d}" in ep10


def test_slice_keys_stay_together_in_part_order() -> None:
    parent = make_natural_key(r"D:\show\movie.mp4")
    first = child_natural_key(parent, 1)
    second = child_natural_key(parent, 2)
    tenth = child_natural_key(parent, 10)
    other = make_natural_key(r"D:\show\movie2.mp4")
    assert parent < first < second < tenth
    assert tenth < other


def test_settings_keep_the_four_order_names(tmp_path: Path) -> None:
    path = tmp_path / "upload.toml"
    path.write_text('chat_id = -100\nschedule_order = "natural"\n', encoding="utf-8")
    hub = SettingsHub(path, tmp_path)
    assert hub.get().schedule_order == "natural"
    payload = hub.public_dict()
    payload["schedule_order"] = "created"
    saved = hub.save_from_payload(payload, expected_revision=payload["revision"])
    assert saved["schedule_order"] == "created"
    assert 'schedule_order = "created"' in path.read_text(encoding="utf-8")
    payload = hub.public_dict()
    payload["schedule_order"] = "nope"
    saved = hub.save_from_payload(payload, expected_revision=payload["revision"])
    assert saved["schedule_order"] == "oldest"
    assert 'schedule_order = "oldest"' in render_upload_toml({"chat_id": -100})


async def _repo(tmp_path: Path) -> TaskRepository:
    await open_pool(tmp_path / "app.db")
    async with get_db() as database:
        await database.executescript(SCHEMA)
        await database.commit()
    return TaskRepository()


async def test_fetch_uses_each_schedule_order(tmp_path: Path) -> None:
    try:
        repo = await _repo(tmp_path)
        ep10 = await repo.add_task(
            file_path=str(tmp_path / "EP10.mp4"),
            file_name="EP10.mp4",
            file_size=50,
            folder_name="show",
            chat_id=-100,
        )
        ep2 = await repo.add_task(
            file_path=str(tmp_path / "EP2.mp4"),
            file_name="EP2.mp4",
            file_size=10,
            folder_name="show",
            chat_id=-100,
        )
        async with get_db() as database:
            await database.execute("UPDATE upload_tasks SET source_born_at = ? WHERE id = ?", (200.0, ep10))
            await database.execute("UPDATE upload_tasks SET source_born_at = ? WHERE id = ?", (100.0, ep2))
            await database.commit()
        oldest = [int(row["id"]) for row in await repo.fetch_pending_tasks(10, order="oldest")]
        natural = [int(row["id"]) for row in await repo.fetch_pending_tasks(10, order="natural")]
        created = [int(row["id"]) for row in await repo.fetch_pending_tasks(10, order="created")]
        smallest = [int(row["id"]) for row in await repo.fetch_pending_tasks(10, order="smallest")]
        assert oldest == [ep10, ep2]
        assert natural == [ep2, ep10]
        assert created == [ep2, ep10]
        assert smallest == [ep2, ep10]
    finally:
        await close_pool()


async def test_same_size_uses_created_time_and_parts_follow_the_parent(tmp_path: Path) -> None:
    try:
        repo = await _repo(tmp_path)
        later = await repo.add_task(
            file_path=str(tmp_path / "later.mp4"),
            file_name="later.mp4",
            file_size=20,
            folder_name="show",
            chat_id=-100,
        )
        earlier = await repo.add_task(
            file_path=str(tmp_path / "earlier.mp4"),
            file_name="earlier.mp4",
            file_size=20,
            folder_name="show",
            chat_id=-100,
        )
        async with get_db() as database:
            await database.execute("UPDATE upload_tasks SET source_born_at = ? WHERE id = ?", (300.0, later))
            await database.execute("UPDATE upload_tasks SET source_born_at = ? WHERE id = ?", (50.0, earlier))
            await database.commit()
        parent = await repo.get_task_by_id(earlier)
        assert parent is not None
        part2 = await repo.insert_slice_child(
            parent,
            part_index=2,
            part_count=2,
            file_path=str(tmp_path / "earlier.part2.mp4"),
            file_name="earlier.part2.mp4",
            file_size=1,
            caption="",
            status="pending",
            after_success="keep",
            single_page=0,
            content_page=0,
        )
        part1 = await repo.insert_slice_child(
            parent,
            part_index=1,
            part_count=2,
            file_path=str(tmp_path / "earlier.part1.mp4"),
            file_name="earlier.part1.mp4",
            file_size=19,
            caption="",
            status="pending",
            after_success="keep",
            single_page=0,
            content_page=0,
        )
        size_order = [int(row["id"]) for row in await repo.fetch_pending_tasks(10, order="smallest")]
        assert size_order.index(earlier) < size_order.index(later)
        natural = [int(row["id"]) for row in await repo.fetch_pending_tasks(10, order="natural")]
        assert natural.index(part1) < natural.index(part2)
    finally:
        await close_pool()


async def test_backfill_fills_only_unfinished_rows(tmp_path: Path) -> None:
    try:
        await open_pool(tmp_path / "app.db")
        video = tmp_path / "EP2.mp4"
        video.write_bytes(b"x")
        async with get_db() as database:
            await database.executescript(SCHEMA)
            await database.execute(
                """INSERT INTO upload_tasks (task_id, file_path, file_name, file_size, chat_id, status)
                   VALUES ('p', ?, 'EP2.mp4', 3, -100, 'pending')""",
                (str(video),),
            )
            await database.execute(
                """INSERT INTO upload_tasks (task_id, file_path, file_name, file_size, chat_id, status)
                   VALUES ('s', ?, 'done.mp4', 3, -100, 'success')""",
                (str(tmp_path / "done.mp4"),),
            )
            await database.commit()
            await _backfill_schedule_keys(database)
            await database.commit()
            async with database.execute(
                "SELECT status, natural_key, source_born_at FROM upload_tasks ORDER BY id"
            ) as cursor:
                pending, done = await cursor.fetchall()
        assert pending[1] == make_natural_key(str(video))
        assert pending[2] is not None and float(pending[2]) < 9.0e15
        assert done[1] is None
        assert done[2] is None
    finally:
        await close_pool()
