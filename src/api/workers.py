"""Worker 列表快照。FastAPI 只拿 dict，避免 API 层 import UploadWorker。

包含磁盘上的 session（含已禁用）和内存里正在跑的 worker。
"""

from __future__ import annotations

import time


def snapshot_workers(scheduler, session_pool, disabled) -> list[dict]:
    discovered = session_pool.list_files()
    names = sorted(set(discovered) | set(scheduler.worker_map))
    items: list[dict] = []
    for name in names:
        worker = scheduler.worker_map.get(name)
        remaining = 0.0
        queue_size = 0
        in_flight = 0
        accepting = False
        if worker is not None:
            remaining = max(0.0, worker.flood_wait_until - time.monotonic())
            queue_size = worker.task_queue.qsize()
            in_flight = len(worker.background_tasks)
            accepting = worker.is_accepting()
        reconnect = session_pool.reconnect_snapshot(name)
        items.append(
            {
                "name": name,
                "username": session_pool.usernames.get(name),
                "enabled": name not in disabled,
                "running": worker is not None,
                "accepting": accepting,
                "flood_wait_seconds": round(remaining, 1),
                "queue_size": queue_size,
                "in_flight": in_flight,
                **reconnect,
            }
        )
    return items
