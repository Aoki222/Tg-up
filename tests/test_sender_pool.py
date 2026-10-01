import asyncio
import time
from unittest.mock import MagicMock

from src.utils.sender_pool import SenderPool


class _MockSender:
    def __init__(self, sender_id: int):
        self.sender_id = sender_id
        self._connected = True
        self.disconnect_calls = 0
        self.connect_calls = 0

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, *_args, **_kwargs):
        self._connected = True
        self.connect_calls += 1

    async def disconnect(self):
        self._connected = False
        self.disconnect_calls += 1


async def test_sender_pool_acquire_and_reuse():
    created_count = 0

    async def mock_factory(_client):
        nonlocal created_count
        created_count += 1
        return _MockSender(created_count)

    client = MagicMock()
    pool = SenderPool(client, max_senders=4, sender_factory=mock_factory)

    # 1. 首次借用 2 条连接 -> 新建 2 条
    s1 = await pool.acquire(2)
    assert len(s1) == 2
    assert created_count == 2
    assert pool.active_count == 2
    assert pool.idle_count == 0

    # 2. 归还这 2 条连接 -> 放入空闲池
    await pool.release(s1)
    assert pool.active_count == 0
    assert pool.idle_count == 2

    # 3. 再次借用 2 条连接 -> 复用已有连接，不新建
    s2 = await pool.acquire(2)
    assert len(s2) == 2
    assert created_count == 2  # 未新增创建！
    assert set(s.sender_id for s in s2) == set(s.sender_id for s in s1)

    # 4. 再次借用超过空闲数的连接 (申请 3 条) -> 1 条新建，但受上限 max_senders=4 控制
    s3 = await pool.acquire(3)
    # 当前 active=2, max=4, 只能新建 2 条
    assert len(s3) == 2
    assert created_count == 4

    await pool.release(s2)
    await pool.release(s3)
    assert pool.idle_count == 4


async def test_sender_pool_dynamic_count():
    created_count = 0

    async def mock_factory(_client):
        nonlocal created_count
        created_count += 1
        return _MockSender(created_count)

    client = MagicMock()
    pool = SenderPool(client, max_senders=6, sender_factory=mock_factory)

    # 仅申请 1 条 (如封面小图) -> 只建 1 条
    single = await pool.acquire(1)
    assert len(single) == 1
    assert created_count == 1
    await pool.release(single)
    assert pool.idle_count == 1


async def test_sender_pool_idle_timeout():
    created_count = 0

    async def mock_factory(_client):
        nonlocal created_count
        created_count += 1
        return _MockSender(created_count)

    client = MagicMock()
    # 设定 1 秒超时
    pool = SenderPool(client, max_senders=2, idle_timeout=0.1, sender_factory=mock_factory)

    senders = await pool.acquire(1)
    sender = senders[0]
    await pool.release(senders)
    assert pool.idle_count == 1

    # 模拟经过 0.2 秒后超时
    await asyncio.sleep(0.15)

    # 再次 acquire，超时的 sender 应该被 disconnect 并淘汰，重新建连
    new_senders = await pool.acquire(1)
    assert len(new_senders) == 1
    assert sender.disconnect_calls == 1  # 旧 sender 被释放
    assert new_senders[0].sender_id != sender.sender_id
    assert created_count == 2

    await pool.release(new_senders)


async def test_sender_pool_invalidate_all():
    created_count = 0

    async def mock_factory(_client):
        nonlocal created_count
        created_count += 1
        return _MockSender(created_count)

    client = MagicMock()
    pool = SenderPool(client, max_senders=3, sender_factory=mock_factory)

    senders = await pool.acquire(2)
    await pool.release(senders)
    assert pool.idle_count == 2

    # 断线时清空所有空闲连接
    await pool.invalidate_all()
    assert pool.idle_count == 0
    # 确认都被断开了
    for s in senders:
        assert s.disconnect_calls >= 1


async def test_sender_pool_close():
    created_count = 0

    async def mock_factory(_client):
        nonlocal created_count
        created_count += 1
        return _MockSender(created_count)

    client = MagicMock()
    pool = SenderPool(client, max_senders=3, sender_factory=mock_factory)

    idle_senders = await pool.acquire(2)
    await pool.release(idle_senders[:1])  # 1 个 idle, 1 个 active
    assert pool.idle_count == 1
    assert pool.active_count == 1

    await pool.close()
    assert pool.idle_count == 0
    assert pool.active_count == 0
    for s in idle_senders:
        assert s.disconnect_calls >= 1

    # 关闭后再 acquire 返回空列表
    assert await pool.acquire(1) == []
