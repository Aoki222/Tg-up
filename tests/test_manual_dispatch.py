from dataclasses import replace
from pathlib import Path
import pytest

from src.adapters.progress import ProgressHub
from src.api.app import create_api
from src.api.routes.tasks import ManualDispatchBody
from src.domain.caption import build_ingest_caption
from src.domain.task import AfterSuccess, TaskPolicy
from src.domain.upload_settings import FolderRoute, PreviewMode, UploadSettings
from src.pipeline.ingest.ingestor import FileIngestor
from tests.test_ingestor import FakeHub, FakeRepo, FakeRescheduler, _settings


def _create_env(tmp_path: Path, **kwargs) -> tuple[FileIngestor, FakeRepo, FakeRescheduler, FakeHub]:
    kwargs.setdefault("observer_paths", (tmp_path,))
    repo = FakeRepo()
    rescheduler = FakeRescheduler()
    hub = FakeHub(_settings(**kwargs))
    ingestor = FileIngestor(repo, rescheduler, hub, concurrency=8)
    return ingestor, repo, rescheduler, hub


async def test_manual_dispatch_rejects_path_outside_roots(tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    video = outside / "video.mp4"
    video.write_bytes(b"data")
    ingestor, repo, rescheduler, _ = _create_env(tmp_path, observer_paths=(watch,))
    res = await ingestor.manual_dispatch(video, chat_id=-100)
    assert res["ok"] is False
    assert "监听目录" in res["reason"]
    assert len(repo.tasks) == 0
    assert rescheduler.calls == 0


async def test_manual_dispatch_rejects_symlink_escape(tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"hidden")
    link = watch / "link.mp4"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("当前环境不能创建符号链接")
    ingestor, repo, _, _ = _create_env(tmp_path, observer_paths=(watch,))
    res = await ingestor.manual_dispatch(link, chat_id=-100)
    assert res["ok"] is False
    assert "监听目录" in res["reason"]
    assert len(repo.tasks) == 0


async def test_stop_puts_sentinel_into_full_queue(tmp_path: Path) -> None:
    import asyncio

    ingestor, _, _, _ = _create_env(tmp_path)
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    await queue.put(tmp_path / "waiting.mp4")
    ingestor._file_queue = queue
    await ingestor.stop()
    assert queue.get_nowait() is None


async def test_manual_dispatch_file_not_found(tmp_path: Path) -> None:
    ingestor, repo, rescheduler, _ = _create_env(tmp_path)
    res = await ingestor.manual_dispatch(tmp_path / "non_existent.mp4", chat_id=-100)
    assert res["ok"] is False
    assert "不存在" in res["reason"]
    assert len(repo.tasks) == 0
    assert rescheduler.calls == 0


async def test_manual_dispatch_transient_file(tmp_path: Path) -> None:
    ingestor, repo, rescheduler, _ = _create_env(tmp_path)
    tmp_file = tmp_path / "download.mp4.crdownload"
    tmp_file.write_bytes(b"data")
    res = await ingestor.manual_dispatch(tmp_file, chat_id=-100)
    assert res["ok"] is False
    assert "临时文件" in res["reason"]
    assert len(repo.tasks) == 0


async def test_manual_dispatch_active_task_dedup(tmp_path: Path) -> None:
    ingestor, repo, rescheduler, _ = _create_env(tmp_path)
    file = tmp_path / "video.mp4"
    file.write_bytes(b"data")
    repo.active[str(file.resolve())] = 999

    res = await ingestor.manual_dispatch(file, chat_id=-100)
    assert res["ok"] is False
    assert "已有未完成任务" in res["reason"]
    assert res["task_id"] == 999
    assert len(repo.tasks) == 0


async def test_manual_dispatch_unmatched_without_chat_id_rejected(tmp_path: Path) -> None:
    # 没有配置任何路由，且调用时未指定 chat_id
    ingestor, repo, rescheduler, _ = _create_env(tmp_path, routes=())
    file = tmp_path / "video.mp4"
    file.write_bytes(b"data")

    res = await ingestor.manual_dispatch(file)
    assert res["ok"] is False
    assert "未指定目标群组且该路径未匹配任何路由" in res["reason"]
    assert len(repo.tasks) == 0


async def test_manual_dispatch_explicit_chat_id_success(tmp_path: Path) -> None:
    # 没有配置路由，但显式指定了 chat_id
    ingestor, repo, rescheduler, hub = _create_env(tmp_path, routes=())
    file = tmp_path / "video.mp4"
    file.write_bytes(b"data")

    res = await ingestor.manual_dispatch(file, chat_id=-100888)
    assert res["ok"] is True
    assert res["status"] == "pending"
    assert len(repo.tasks) == 1
    task = repo.tasks[0]
    assert task["chat_id"] == -100888
    assert task["file_path"] == str(file.resolve())
    assert rescheduler.calls == 1
    # 策略快照已保存
    assert 1 in hub.policies


async def test_manual_dispatch_route_inheritance_even_when_disabled(tmp_path: Path) -> None:
    # 路由被禁用，自动监控不会理会，但手动投递可自动继承其 chat_id
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    route = FolderRoute(path=sub_dir.resolve(), chat_id=-100555, dest_id="-100555", enabled=False)
    ingestor, repo, rescheduler, _ = _create_env(tmp_path, routes=(route,))

    file = sub_dir / "clip.mp4"
    file.write_bytes(b"clip content")

    res = await ingestor.manual_dispatch(file)
    assert res["ok"] is True
    assert res["status"] == "pending"
    assert len(repo.tasks) == 1
    task = repo.tasks[0]
    assert task["chat_id"] == -100555
    assert rescheduler.calls == 1


async def test_manual_dispatch_custom_options(tmp_path: Path) -> None:
    ingestor, repo, rescheduler, hub = _create_env(tmp_path)
    file = tmp_path / "movie.mp4"
    file.write_bytes(b"movie content")

    res = await ingestor.manual_dispatch(
        file,
        chat_id=-100123,
        topic_id=456,
        caption="自定义说明文案",
        after_success="delete",
        need_preview=False,
    )
    assert res["ok"] is True
    assert len(repo.tasks) == 1
    task = repo.tasks[0]
    assert task["chat_id"] == -100123
    assert task["topic_id"] == 456
    assert task["caption"] == "自定义说明文案"
    assert task["after_success"] == "delete"
    assert task["single_page"] is False
    assert task["content_page"] is False

    policy = hub.policies[task_id := res["task_id"]]
    assert policy.after_success == AfterSuccess.DELETE
    assert policy.need_preview is False


async def test_manual_dispatch_oversized(tmp_path: Path) -> None:
    offered = []

    async def on_oversized(task_id: int):
        offered.append(task_id)

    repo = FakeRepo()
    rescheduler = FakeRescheduler()
    hub = FakeHub(_settings(observer_paths=(tmp_path,)))
    ingestor = FileIngestor(repo, rescheduler, hub, concurrency=8, on_oversized=on_oversized)

    file = tmp_path / "large.mp4"
    file.write_bytes(b"0" * 10)  # mock 大小

    import stat as stat_mod
    import unittest.mock as mock
    with mock.patch("pathlib.Path.stat") as mock_stat:
        mock_stat_res = mock.MagicMock()
        mock_stat_res.st_mode = stat_mod.S_IFREG
        mock_stat_res.st_size = 2500 * 1024 * 1024  # 2.5 GB
        mock_stat.return_value = mock_stat_res

        res = await ingestor.manual_dispatch(file, chat_id=-100)

    assert res["ok"] is True
    assert res["status"] == "oversized"
    assert len(repo.tasks) == 1
    task = repo.tasks[0]
    assert task["status"] == "oversized"
    assert offered == [res["task_id"]]
    assert rescheduler.calls == 0


async def test_api_dispatch_endpoint(tmp_path: Path) -> None:
    ingestor, repo, rescheduler, _ = _create_env(tmp_path)

    app = create_api(
        progress_hub=ProgressHub(),
        ingestor=ingestor,
    )

    def find_endpoint(path: str, method: str):
        for route in app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route.endpoint
        raise KeyError((path, method))

    endpoint = find_endpoint("/api/tasks/dispatch", "POST")

    f1 = tmp_path / "video1.mp4"
    f1.write_bytes(b"v1")
    f2 = tmp_path / "video2.mp4"
    f2.write_bytes(b"v2")
    non_existent = tmp_path / "none.mp4"

    # 1. 批量投递：2 个成功，1 个不存在跳过
    body = ManualDispatchBody(
        file_paths=[str(f1), str(f2), str(non_existent)],
        chat_id=-100888,
        caption="批量说明",
    )
    result = await endpoint(body)

    assert result["ok"] is True
    assert result["total"] == 3
    assert result["dispatched_count"] == 2
    assert result["skipped_count"] == 1
    assert len(result["dispatched"]) == 2
    assert len(result["skipped"]) == 1
    assert "不存在" in result["skipped"][0]["reason"]
    assert len(repo.tasks) == 2

    # 2. 单文件兼容字段 file_path 且防重测试
    body_single = ManualDispatchBody(
        file_path=str(f1),
        chat_id=-100888,
    )
    result_single = await endpoint(body_single)
    # 之前 f1 已经入库并在 active 中，此处应被防重拦截为 skipped
    assert result_single["dispatched_count"] == 0
    assert result_single["skipped_count"] == 1
    assert "已有未完成任务" in result_single["skipped"][0]["reason"]
