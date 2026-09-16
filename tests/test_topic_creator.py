import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.utils.topic_creactor import TopicCreator


class FakeTopicRepo:
    def __init__(self):
        self.topics: dict[tuple[int, str], int] = {}

    async def get_chat_topic(self, chat_id: int, topic_path: str) -> int | None:
        return self.topics.get((chat_id, topic_path))

    async def save_chat_topic(self, chat_id: int, topic_id: int, topic_path: str) -> None:
        self.topics[(chat_id, topic_path)] = topic_id


class BlockingClient:
    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0
        self.creates = 0
        self.release = asyncio.Event()

    async def __call__(self, _request):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await self.release.wait()
            self.creates += 1
            return SimpleNamespace(updates=[SimpleNamespace(message=SimpleNamespace(id=self.creates))])
        finally:
            self.in_flight -= 1


async def test_existing_topic_skips_telegram(tmp_path: Path) -> None:
    repo = FakeTopicRepo()
    folder = tmp_path / "show"
    folder.mkdir()
    repo.topics[(-100, str(folder.resolve()))] = 42
    calls = {"n": 0}

    class Boom:
        async def __call__(self, _request):
            calls["n"] += 1
            raise AssertionError("should not create")

    creator = TopicCreator(lambda: Boom(), repo)
    topic_id = await creator.get_or_create_topic(str(folder), "show", -100)
    assert topic_id == 42
    assert calls["n"] == 0


async def test_same_folder_creates_once(tmp_path: Path) -> None:
    repo = FakeTopicRepo()
    folder = tmp_path / "same"
    folder.mkdir()
    client = BlockingClient()
    creator = TopicCreator(lambda: client, repo)
    first = asyncio.create_task(creator.get_or_create_topic(str(folder), "same", -100))
    second = asyncio.create_task(creator.get_or_create_topic(str(folder), "same", -100))
    for _ in range(50):
        if client.max_in_flight == 1 and client.in_flight == 1:
            break
        await asyncio.sleep(0.01)
    assert client.max_in_flight == 1
    client.release.set()
    ids = await asyncio.gather(first, second)
    assert ids == [1, 1]
    assert client.creates == 1


async def test_different_folders_create_in_parallel(tmp_path: Path) -> None:
    repo = FakeTopicRepo()
    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()
    client = BlockingClient()
    creator = TopicCreator(lambda: client, repo)
    first = asyncio.create_task(creator.get_or_create_topic(str(folder_a), "a", -100))
    second = asyncio.create_task(creator.get_or_create_topic(str(folder_b), "b", -100))
    for _ in range(50):
        if client.max_in_flight == 2:
            break
        await asyncio.sleep(0.01)
    assert client.max_in_flight == 2
    client.release.set()
    ids = await asyncio.gather(first, second)
    assert sorted(ids) == [1, 2]
    assert client.creates == 2
