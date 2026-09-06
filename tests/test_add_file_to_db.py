import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from main import Uploader


@pytest.fixture
def uploader():
    with patch("main.FolderWatcher"):
        u = Uploader()
    u.queue.task_done = MagicMock()
    return u


async def _run_consume_all(uploader):
    original_get = uploader.queue.get

    async def controlled_get():
        if uploader.queue.empty():
            raise asyncio.CancelledError()
        return await original_get()

    with patch.object(uploader.queue, "get", side_effect=controlled_get):
        try:
            await uploader.add_file_to_db()
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_add_file_to_db_success(uploader):
    test_task = {
        "file_path": "/path/to/test.txt",
        "file_name": "test.txt",
        "file_size": 1024,
    }

    with patch("main.insert_single_task", AsyncMock(return_value=(10, 1))) as mock_insert:
        await uploader.queue.put(test_task)
        await _run_consume_all(uploader)

    mock_insert.assert_called_once_with(
        file_path="/path/to/test.txt",
        file_name="test.txt",
        file_size=1024,
    )


@pytest.mark.asyncio
async def test_add_file_to_db_exception(uploader):
    test_task = {
        "file_path": "/path/to/test.txt",
        "file_name": "test.txt",
        "file_size": 1024,
    }

    with patch("main.insert_single_task", AsyncMock(side_effect=Exception("Database error"))):
        await uploader.queue.put(test_task)
        await _run_consume_all(uploader)


@pytest.mark.asyncio
async def test_add_file_to_db_multiple_tasks(uploader):
    tasks = [
        {"file_path": f"/path/to/file{i}.txt", "file_name": f"file{i}.txt", "file_size": 1024 * i}
        for i in range(1, 4)
    ]

    with patch("main.insert_single_task", AsyncMock(return_value=(10, 1))) as mock_insert:
        for t in tasks:
            await uploader.queue.put(t)
        await _run_consume_all(uploader)

    assert mock_insert.call_count == 3


@pytest.mark.asyncio
async def test_add_file_to_db_logs_on_exception(uploader, caplog):
    import logging

    test_task = {
        "file_path": "/path/to/test.txt",
        "file_name": "test.txt",
        "file_size": 1024,
    }

    with patch("main.insert_single_task", AsyncMock(side_effect=Exception("Database error"))):
        await uploader.queue.put(test_task)
        with caplog.at_level(logging.ERROR):
            await _run_consume_all(uploader)

    assert "Database error" in caplog.text
