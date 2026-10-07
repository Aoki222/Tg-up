"""upload_tasks / upload_slices 的写入口。

返回 dict 行，由调用方装配成领域 Task。
认领、回 pending、标成功都必须带状态条件或清掉 assigned_worker。
新写入只维护 platform / dest_id / dest_extra / assigned_worker / remote_id。
chat_id 因 NOT NULL 仍写入；topic_id、assigned_bot、telegram_msg_id 只留给旧行回填，不再更新。

chat_topic 的 SQL 在 adapters.db.topics。这里的两个方法只是转调，避免一次改完所有调用方。
"""

import uuid

from pathlib import Path

from ..database.connection import get_db
from ..domain import limits
from ..domain.slices import MSG_CUTTING, MSG_WAIT
from .db.topics import get_chat_topic, save_chat_topic


def _bot_limit() -> int:
    return limits.TELEGRAM_BOT_MAX_BYTES


async def _requeue_ids(database, task_ids: list[int], status: str, error_message: str) -> int:
    updated = 0
    for offset in range(0, len(task_ids), 400):
        chunk = task_ids[offset : offset + 400]
        placeholders = ",".join("?" * len(chunk))
        cursor = await database.execute(
            f"""UPDATE upload_tasks SET status = ?, retry_count = 0,
                assigned_worker = NULL, assigned_at = NULL, started_at = NULL,
                finished_at = NULL, error_msg = ?
                WHERE status = 'failed' AND id IN ({placeholders})""",
            (status, error_message, *chunk),
        )
        updated += cursor.rowcount
    return updated


class TaskRepository:
    """upload_tasks 持久化。返回行字典，领域 Task 由调用方装配。"""

    async def add_task(
        self,
        file_path: str,
        file_name: str,
        file_size: int,
        folder_name: str,
        chat_id: int,
        single_page: bool = False,
        content_page: bool = False,
        status: str = "pending",
        max_retries: int = 3,
        topic_id: int | None = None,
        caption: str = "",
        after_success: str = "keep",
        platform: str = "telegram",
        dest_id: str | None = None,
        error_msg: str | None = None,
    ) -> int:
        """插入一条任务。封面需求和 after_success 入库时拍快照。"""
        async with get_db() as database:
            cursor = await database.execute(
                """INSERT INTO upload_tasks
                (
                   task_id,
                   file_path,
                   file_name,
                   folder_name,
                   file_size,
                   chat_id,
                   caption,
                   single_page,
                   content_page,
                   page_path,
                   status,
                   max_retries,
                   after_success,
                   platform,
                   dest_id,
                   dest_extra,
                   error_msg
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    file_path,
                    file_name,
                    folder_name,
                    file_size,
                    chat_id,
                    caption,
                    int(single_page),
                    int(content_page),
                    status,
                    max_retries,
                    after_success,
                    platform,
                    dest_id if dest_id else (str(chat_id) if platform == "telegram" else ""),
                    str(topic_id) if topic_id is not None else None,
                    error_msg,
                ),
            )
            await database.commit()
            task_id = cursor.lastrowid
            if task_id is None:
                raise RuntimeError("入库失败：未返回任务 id")
            return int(task_id)

    async def find_active_by_file_path(self, file_path: str) -> int | None:
        """未完成任务按绝对路径去重。success/failed 的同路径允许再来一条。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT id FROM upload_tasks
                   WHERE file_path = ? AND status NOT IN ('success', 'failed')
                   LIMIT 1""",
                (file_path,),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else None

    async def get_chat_topic(self, chat_id: int, topic_path: str) -> int | None:
        """转调 adapters.db.topics。topic_path 是目录绝对路径。"""
        return await get_chat_topic(chat_id, topic_path)

    async def save_chat_topic(self, chat_id: int, topic_id: int, topic_path: str) -> None:
        """转调 adapters.db.topics。重复键更新 topic_id。"""
        await save_chat_topic(chat_id, topic_id, topic_path)

    async def count_active_tasks(self, worker_name: str) -> int:
        """assigned + uploading 都占槽。只数内存队列会在崩溃后低估负载。"""
        counts = await self.count_active_by_workers()
        return int(counts.get(worker_name, 0))

    async def count_active_by_workers(self) -> dict[str, int]:
        """一次查出每个 worker 的 assigned+uploading 数量。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT COALESCE(assigned_worker, assigned_bot) AS worker, COUNT(*) AS n
                   FROM upload_tasks
                   WHERE status IN ('assigned', 'uploading')
                     AND COALESCE(assigned_worker, assigned_bot) IS NOT NULL
                   GROUP BY worker"""
            ) as cursor:
                rows = await cursor.fetchall()
                return {str(row[0]): int(row[1]) for row in rows}

    async def fetch_pending_tasks(self, limit: int) -> list[dict]:
        """只捞 Telegram 的 pending。oversized 不在这里，避免自动分给 Bot。小文件优先。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT * FROM upload_tasks
                   WHERE status IN ('pending', 'retrying')
                     AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                   ORDER BY file_size ASC LIMIT ?""",
                (limit,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def claim_task(self, task_id: int, worker_name: str) -> bool:
        """用带状态条件的 CAS 抢占 pending 任务，成功后归属指定 Worker。"""
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET status = 'assigned',
                   assigned_worker = ?, assigned_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status IN ('pending', 'retrying')
                     AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'""",
                (worker_name, task_id),
            )
            await database.commit()
            return cursor.rowcount > 0

    async def claim_oversized(self, task_id: int, worker_name: str) -> bool:
        """手动把 oversized 交给个人号。只有当前仍是 oversized 才能抢走。"""
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET status = 'assigned',
                   assigned_worker = ?, assigned_at = CURRENT_TIMESTAMP, error_msg = NULL
                   WHERE id = ? AND status = 'oversized' AND parent_id IS NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM upload_slices WHERE parent_id = upload_tasks.id
                     )
                     AND COALESCE(error_msg, '') NOT IN (?, ?)
                     AND COALESCE(error_msg, '') NOT LIKE '第 %段%'
                     AND COALESCE(error_msg, '') NOT LIKE '已分成 %段，分段已进入队列'""",
                (worker_name, task_id, MSG_WAIT, MSG_CUTTING),
            )
            await database.commit()
            return cursor.rowcount > 0

    async def park_oversized(self, task_id: int, error_message: str) -> None:
        """停在 oversized，清空归属，不增加 retry_count。"""
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = 'oversized', error_msg = ?,
                   assigned_worker = NULL, assigned_at = NULL, started_at = NULL
                   WHERE id = ?""",
                (error_message[:500], task_id),
            )
            await database.commit()

    async def mark_task_uploading(self, task_id: int) -> None:
        """只有 assigned 才能进入 uploading，防止对账打回 pending 后还被标成在传。"""
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = 'uploading', started_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status = 'assigned'""",
                (task_id,),
            )
            await database.commit()

    async def mark_task_succeeded(self, task_id: int, telegram_message_id: int) -> None:
        """将上传中的任务标记为成功，并记录 Telegram 远端消息 ID。"""
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET status = 'success', finished_at = CURRENT_TIMESTAMP,
                   remote_id = ? WHERE id = ?""",
                (str(telegram_message_id), task_id),
            )
            await database.commit()

    async def mark_task_failed(self, task_id: int, retry_count: int, max_retries: int, error_message: str) -> None:
        """未超限回 pending 并清空归属。超过 Bot 上限则停在 oversized，不换 Bot。"""
        limit = _bot_limit()
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET
                   status = CASE
                       WHEN parent_id IS NOT NULL AND ? >= ? THEN 'failed'
                       WHEN parent_id IS NOT NULL THEN 'pending'
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN 'oversized'
                       WHEN ? >= ? THEN 'failed'
                       ELSE 'pending'
                   END,
                   retry_count = ?, error_msg = ?,
                   assigned_worker = NULL, assigned_at = NULL, started_at = NULL
                   WHERE id = ?""",
                (
                    retry_count,
                    max_retries,
                    limit,
                    retry_count,
                    max_retries,
                    retry_count,
                    error_message[:500],
                    task_id,
                ),
            )
            await database.commit()

    async def release_task(self, task_id: int, error_message: str) -> None:
        """回 pending 且不增加 retry_count。超过 Bot 上限则改为 oversized。"""
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET
                   status = CASE
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN 'oversized'
                       ELSE 'pending'
                   END,
                   error_msg = ?,
                   assigned_worker = NULL, assigned_at = NULL, started_at = NULL
                   WHERE id = ? AND status IN ('assigned', 'uploading')""",
                (_bot_limit(), error_message[:500], task_id),
            )
            await database.commit()

    async def release_tasks_for_worker(self, worker_name: str, error_message: str) -> list[int]:
        """这个号名下还挂着的 assigned/uploading 全部释放，不增加 retry_count。返回被释放的 id。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT id FROM upload_tasks
                   WHERE COALESCE(assigned_worker, assigned_bot) = ?
                     AND status IN ('assigned', 'uploading')""",
                (worker_name,),
            ) as cursor:
                task_ids = [int(row[0]) for row in await cursor.fetchall()]
            if not task_ids:
                return []
            await database.execute(
                """UPDATE upload_tasks SET
                   status = CASE
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN 'oversized'
                       ELSE 'pending'
                   END,
                   error_msg = ?,
                   assigned_worker = NULL, assigned_at = NULL, started_at = NULL
                   WHERE COALESCE(assigned_worker, assigned_bot) = ? AND status IN ('assigned', 'uploading')""",
                (_bot_limit(), error_message[:500], worker_name),
            )
            await database.commit()
            return task_ids

    async def list_failed_root_ids(self) -> list[int]:
        """失败列里的源任务。分段失败行另算。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT id FROM upload_tasks
                   WHERE status = 'failed' AND parent_id IS NULL
                   ORDER BY id ASC"""
            ) as cursor:
                return [int(row[0]) for row in await cursor.fetchall()]

    async def list_oversized_root_ids(self) -> list[int]:
        """过大列里的源文件。分段子任务不算。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT id FROM upload_tasks
                   WHERE status = 'oversized' AND parent_id IS NULL
                   ORDER BY id ASC"""
            ) as cursor:
                return [int(row[0]) for row in await cursor.fetchall()]

    async def list_success_page(
        self, *, scope: str, page: int, page_size: int
    ) -> tuple[list[dict], int]:
        """成功记录分页。源文件那条切片记账不计入，各段计入。"""
        where = """status = 'success'
                   AND NOT (parent_id IS NULL AND COALESCE(slice_parts, 0) > 0)"""
        if scope == "today":
            where += " AND date(finished_at, 'localtime') = date('now', 'localtime')"
        offset = max(0, page - 1) * page_size
        async with get_db() as database:
            async with database.execute(
                f"SELECT COUNT(*) FROM upload_tasks WHERE {where}"
            ) as cursor:
                row = await cursor.fetchone()
                total = int(row[0] if row else 0)
            async with database.execute(
                f"""SELECT id, file_name, file_size, folder_name, finished_at,
                           part_index, part_count
                    FROM upload_tasks
                    WHERE {where}
                    ORDER BY finished_at IS NULL, finished_at DESC, id DESC
                    LIMIT ? OFFSET ?""",
                (page_size, offset),
            ) as cursor:
                items = [dict(row) for row in await cursor.fetchall()]
        return items, total

    async def reconcile_stale_tasks(self) -> int:
        """进程刚起来时内存队列是空的，assigned / uploading 都是幽灵任务。"""
        limit = _bot_limit()
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET
                   status = CASE
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN 'oversized'
                       ELSE 'pending'
                   END,
                   assigned_worker = NULL,
                   assigned_at = NULL,
                   started_at = NULL,
                   error_msg = CASE
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN COALESCE(NULLIF(error_msg, ''), ?)
                       ELSE 'recovered on startup'
                   END
                   WHERE status IN ('assigned', 'uploading')""",
                (limit, limit, limits.OVERSIZED_REASON),
            )
            await database.commit()
            return cursor.rowcount

    async def recover_timed_out_tasks(
        self,
        uploading_timeout_seconds: int = 1200,
        assigned_timeout_seconds: int = 600,
    ) -> int:
        """运行中兜底：超时的小文件回 pending，超过 Bot 上限的改为 oversized。"""
        limit = _bot_limit()
        async with get_db() as database:
            uploading = await database.execute(
                """UPDATE upload_tasks SET
                   status = CASE
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN 'oversized'
                       ELSE 'pending'
                   END,
                   assigned_worker = NULL,
                   assigned_at = NULL, started_at = NULL, error_msg = 'timeout recovered'
                   WHERE status = 'uploading'
                     AND started_at < datetime('now', ?)""",
                (limit, f"-{uploading_timeout_seconds} seconds"),
            )
            assigned = await database.execute(
                """UPDATE upload_tasks SET
                   status = CASE
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN 'oversized'
                       ELSE 'pending'
                   END,
                   assigned_worker = NULL,
                   assigned_at = NULL, started_at = NULL, error_msg = 'assigned timeout recovered'
                   WHERE status = 'assigned'
                     AND assigned_at < datetime('now', ?)""",
                (limit, f"-{assigned_timeout_seconds} seconds"),
            )
            await database.commit()
            return uploading.rowcount + assigned.rowcount

    async def fetch_preparing_tasks(self) -> list[dict]:
        """启动时把未做完的封面任务重新丢给预览池。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT * FROM upload_tasks
                   WHERE status = 'preparing'
                   ORDER BY id ASC"""
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def update_preview(self, task_id: int, page_path: str | None, success: bool, error_message: str = "") -> None:
        """截图结束的放行点。小文件转 pending；超过 Bot 上限转 oversized，不进调度。"""
        limit = _bot_limit()
        preview_note = "" if success else f"preview failed: {error_message}"[:500]
        oversized_note = preview_note or limits.OVERSIZED_REASON
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_tasks SET page_path = ?,
                   status = CASE
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN 'oversized'
                       ELSE 'pending'
                   END,
                   error_msg = CASE
                       WHEN file_size > ? AND COALESCE(NULLIF(platform, ''), 'telegram') = 'telegram'
                           THEN ?
                       ELSE ?
                   END
                   WHERE id = ?""",
                (page_path, limit, limit, oversized_note[:500], preview_note, task_id),
            )
            await database.commit()

    async def get_task_by_id(self, task_id: int) -> dict | None:
        """Worker 开工前重读，主要是拿截图回填的 page_path。"""
        async with get_db() as database:
            async with database.execute(
                "SELECT * FROM upload_tasks WHERE id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_board_tasks(self, failed_limit: int = 80) -> list[dict]:
        """看板：进行中全量 + 最近失败。切片各段和普通任务一起出现。"""
        async with get_db() as database:
            async with database.execute(
                """SELECT * FROM upload_tasks
                   WHERE status IN ('preparing', 'pending', 'retrying', 'assigned', 'uploading', 'oversized')
                   ORDER BY id ASC"""
            ) as cursor:
                active = [dict(row) for row in await cursor.fetchall()]
            async with database.execute(
                """SELECT * FROM upload_tasks
                   WHERE status = 'failed'
                   ORDER BY id DESC LIMIT ?""",
                (failed_limit,),
            ) as cursor:
                failed = [dict(row) for row in await cursor.fetchall()]
        return active + failed

    async def count_board_statuses(self) -> dict[str, int]:
        """看板列计数。retrying 计入 pending。"""
        counts = {
            "preparing": 0,
            "pending": 0,
            "assigned": 0,
            "uploading": 0,
            "oversized": 0,
            "failed": 0,
            "success": 0,
            "success_today": 0,
        }
        async with get_db() as database:
            async with database.execute(
                """SELECT status, COUNT(*) AS n FROM upload_tasks
                   WHERE status IN (
                       'preparing', 'pending', 'retrying', 'assigned', 'uploading',
                       'oversized', 'failed', 'success'
                     )
                     AND NOT (
                       status = 'success'
                       AND parent_id IS NULL
                       AND COALESCE(slice_parts, 0) > 0
                     )
                   GROUP BY status"""
            ) as cursor:
                rows = await cursor.fetchall()
            async with database.execute(
                """SELECT COUNT(*) FROM upload_tasks
                   WHERE status = 'success'
                     AND NOT (
                       parent_id IS NULL
                       AND COALESCE(slice_parts, 0) > 0
                     )
                     AND date(finished_at, 'localtime') = date('now', 'localtime')"""
            ) as cursor:
                today = await cursor.fetchone()
                counts["success_today"] = int(today[0] if today else 0)
        for row in rows:
            status = str(row[0])
            n = int(row[1])
            if status == "retrying":
                counts["pending"] += n
            elif status in counts:
                counts[status] = n
        return counts

    async def requeue_failed(self, task_id: int) -> str:
        """手动重试一条 failed：次数归零并回 pending。CAS，非 failed 不动。

        返回 ok / not_found / not_failed / missing_file。
        """
        row = await self.get_task_by_id(task_id)
        if row is None:
            return "not_found"
        if str(row.get("status")) != "failed":
            return "not_failed"
        file_path = str(row.get("file_path") or "")
        if not file_path or not Path(file_path).exists():
            return "missing_file"
        platform = str(row.get("platform") or "telegram")
        huge = limits.telegram_bot_blocked(int(row.get("file_size") or 0), platform)
        target = "oversized" if huge else "pending"
        note = limits.OVERSIZED_REASON if huge else "manual retry"
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET status = ?, retry_count = 0,
                   assigned_worker = NULL, assigned_at = NULL, started_at = NULL,
                   finished_at = NULL, error_msg = ?
                   WHERE id = ? AND status = 'failed'""",
                (target, note, task_id),
            )
            await database.commit()
            if cursor.rowcount == 0:
                return "not_failed"
        return "ok"

    async def requeue_all_failed(self) -> tuple[int, int, int]:
        """重置 failed。缺文件跳过；超过 Bot 上限改为 oversized，不回到 Bot。

        返回 (retried, skipped, parked)。
        """
        async with get_db() as database:
            async with database.execute(
                """SELECT id, file_path, file_size, platform FROM upload_tasks
                   WHERE status = 'failed'"""
            ) as cursor:
                rows = await cursor.fetchall()
        ready: list[int] = []
        parked_ids: list[int] = []
        skipped = 0
        for row in rows:
            file_path = str(row[1] or "")
            if not file_path or not Path(file_path).exists():
                skipped += 1
                continue
            if limits.telegram_bot_blocked(int(row[2] or 0), str(row[3] or "telegram")):
                parked_ids.append(int(row[0]))
            else:
                ready.append(int(row[0]))
        retried = 0
        parked = 0
        async with get_db() as database:
            retried = await _requeue_ids(database, ready, "pending", "manual retry")
            parked = await _requeue_ids(database, parked_ids, "oversized", limits.OVERSIZED_REASON)
            await database.commit()
        return retried, skipped, parked

    async def delete_failed(self, task_id: int) -> str:
        """物理删除一条 failed 或 oversized 行。返回 ok / not_found / not_failed。"""
        row = await self.get_task_by_id(task_id)
        if row is None:
            return "not_found"
        if str(row.get("status")) not in {"failed", "oversized"}:
            return "not_failed"
        async with get_db() as database:
            cursor = await database.execute(
                "DELETE FROM upload_tasks WHERE id = ? AND status IN ('failed', 'oversized')",
                (task_id,),
            )
            await database.commit()
            if cursor.rowcount == 0:
                return "not_failed"
        return "ok"

    async def delete_failed_ids(self, task_ids: list[int]) -> int:
        """删除指定 failed 行。非 failed / 不存在的跳过。"""
        ids = [int(task_id) for task_id in task_ids if int(task_id) > 0]
        if not ids:
            return 0
        deleted = 0
        async with get_db() as database:
            for offset in range(0, len(ids), 400):
                chunk = ids[offset : offset + 400]
                placeholders = ",".join("?" * len(chunk))
                cursor = await database.execute(
                    f"DELETE FROM upload_tasks WHERE status = 'failed' AND id IN ({placeholders})",
                    chunk,
                )
                deleted += cursor.rowcount
            await database.commit()
        return deleted

    async def record_unmatched(self, file_path: str, file_name: str, folder_name: str, file_size: int) -> None:
        """没有路由的文件只记账，不进入上传队列。"""
        async with get_db() as database:
            await database.execute(
                """INSERT INTO unmatched_files (file_path, file_name, folder_name, file_size, discovered_at)
                   VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(file_path) DO UPDATE SET
                     file_name = excluded.file_name,
                     folder_name = excluded.folder_name,
                     file_size = excluded.file_size,
                     discovered_at = CURRENT_TIMESTAMP""",
                (file_path, file_name, folder_name, file_size),
            )
            await database.commit()

    async def clear_unmatched(self, file_path: str) -> None:
        async with get_db() as database:
            await database.execute("DELETE FROM unmatched_files WHERE file_path = ?", (file_path,))
            await database.commit()

    async def list_unmatched(self, limit: int = 100) -> list[dict]:
        async with get_db() as database:
            async with database.execute(
                """SELECT id, file_path, file_name, folder_name, file_size, discovered_at
                   FROM unmatched_files ORDER BY id DESC LIMIT ?""",
                (limit,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def delete_all_failed(self) -> int:
        """删除库里全部 failed 行。"""
        async with get_db() as database:
            cursor = await database.execute(
                "DELETE FROM upload_tasks WHERE status = 'failed' AND parent_id IS NULL"
            )
            await database.commit()
            return cursor.rowcount

    async def get_tasks_status_by_paths(self, file_paths: list[str]) -> dict[str, dict]:
        """批量获取文件在 upload_tasks 和 unmatched_files 中的状态。"""
        if not file_paths:
            return {}
        result: dict[str, dict] = {}
        async with get_db() as database:
            for offset in range(0, len(file_paths), 400):
                chunk = file_paths[offset : offset + 400]
                placeholders = ",".join("?" * len(chunk))
                async with database.execute(
                    f"""SELECT file_path, status, error_msg, id
                        FROM upload_tasks
                        WHERE file_path IN ({placeholders})
                        ORDER BY id ASC""",
                    chunk,
                ) as cursor:
                    for row in await cursor.fetchall():
                        r = dict(row)
                        result[r["file_path"]] = {
                            "status": r.get("status") or "pending",
                            "task_id": r.get("id"),
                            "error": r.get("error_msg"),
                        }
                async with database.execute(
                    f"""SELECT file_path
                        FROM unmatched_files
                        WHERE file_path IN ({placeholders})""",
                    chunk,
                ) as cursor:
                    for row in await cursor.fetchall():
                        fp = row["file_path"]
                        if fp not in result:
                            result[fp] = {"status": "unmatched"}
        return result

    async def begin_slice(self, task_id: int, parts: int, message: str) -> bool:
        """只有仍停在过大、且还没开始切片的行才能占住。"""
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET slice_parts = ?, error_msg = ?
                   WHERE id = ? AND status = 'oversized' AND parent_id IS NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM upload_slices WHERE parent_id = upload_tasks.id
                     )
                     AND COALESCE(error_msg, '') NOT IN (?, ?)
                     AND COALESCE(error_msg, '') NOT LIKE '第 %段%'
                     AND COALESCE(error_msg, '') NOT LIKE '已分成 %段，分段已进入队列'""",
                (parts, message[:500], task_id, MSG_WAIT, MSG_CUTTING),
            )
            await database.commit()
            return cursor.rowcount > 0

    async def set_oversized_note(
        self,
        task_id: int,
        message: str,
        *,
        clear_parts: bool = False,
    ) -> None:
        """改过大卡片上的说明。clear_parts 用于切片失败后允许再切一次。"""
        async with get_db() as database:
            if clear_parts:
                await database.execute(
                    """UPDATE upload_tasks SET error_msg = ?, slice_parts = NULL
                       WHERE id = ? AND status = 'oversized'""",
                    (message[:500], task_id),
                )
            else:
                await database.execute(
                    """UPDATE upload_tasks SET error_msg = ?
                       WHERE id = ? AND status = 'oversized'""",
                    (message[:500], task_id),
                )
            await database.commit()

    async def has_slice_plan(self, parent_id: int) -> bool:
        async with get_db() as database:
            async with database.execute(
                "SELECT 1 FROM upload_slices WHERE parent_id = ? LIMIT 1",
                (parent_id,),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def list_slices(self, parent_id: int) -> list[dict]:
        async with get_db() as database:
            async with database.execute(
                "SELECT * FROM upload_slices WHERE parent_id = ? ORDER BY part_index ASC",
                (parent_id,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def insert_slice_plan(self, parent_id: int, segments: list[tuple[str, int]]) -> None:
        count = len(segments)
        async with get_db() as database:
            await database.execute("DELETE FROM upload_slices WHERE parent_id = ?", (parent_id,))
            for index, (path, size) in enumerate(segments, start=1):
                await database.execute(
                    """INSERT INTO upload_slices
                       (parent_id, part_index, part_count, segment_path, segment_size, status)
                       VALUES (?, ?, ?, ?, ?, 'planned')""",
                    (parent_id, index, count, path, int(size)),
                )
            await database.commit()

    async def next_planned_slice(self, parent_id: int) -> dict | None:
        async with get_db() as database:
            async with database.execute(
                """SELECT * FROM upload_slices
                   WHERE parent_id = ? AND status = 'planned'
                   ORDER BY part_index ASC LIMIT 1""",
                (parent_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def failed_slice(self, parent_id: int) -> dict | None:
        async with get_db() as database:
            async with database.execute(
                """SELECT * FROM upload_slices
                   WHERE parent_id = ? AND status = 'failed'
                   ORDER BY part_index ASC LIMIT 1""",
                (parent_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def slice_by_child(self, child_id: int) -> dict | None:
        async with get_db() as database:
            async with database.execute(
                "SELECT * FROM upload_slices WHERE child_task_id = ? LIMIT 1",
                (child_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def mark_slice(self, slice_id: int, status: str, child_task_id: int | None = None) -> None:
        async with get_db() as database:
            if child_task_id is None:
                await database.execute(
                    "UPDATE upload_slices SET status = ? WHERE id = ?",
                    (status, slice_id),
                )
            else:
                await database.execute(
                    "UPDATE upload_slices SET status = ?, child_task_id = ? WHERE id = ?",
                    (status, child_task_id, slice_id),
                )
            await database.commit()

    async def active_slice_child(self, parent_id: int) -> bool:
        async with get_db() as database:
            async with database.execute(
                """SELECT 1 FROM upload_slices AS slices
                   JOIN upload_tasks AS tasks ON tasks.id = slices.child_task_id
                   WHERE slices.parent_id = ? AND slices.status = 'queued'
                     AND tasks.status IN ('pending', 'assigned', 'uploading')
                   LIMIT 1""",
                (parent_id,),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def slices_all_success(self, parent_id: int) -> bool:
        async with get_db() as database:
            async with database.execute(
                """SELECT COUNT(*) AS n,
                          SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS ok
                   FROM upload_slices WHERE parent_id = ?""",
                (parent_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None or int(row[0] or 0) == 0:
            return False
        return int(row[0]) == int(row[1] or 0)

    async def delete_slice_plan(self, parent_id: int) -> None:
        async with get_db() as database:
            await database.execute("DELETE FROM upload_slices WHERE parent_id = ?", (parent_id,))
            await database.commit()

    async def delete_task_ids(self, task_ids: list[int]) -> None:
        ids = [int(task_id) for task_id in task_ids if int(task_id) > 0]
        if not ids:
            return
        async with get_db() as database:
            for offset in range(0, len(ids), 400):
                chunk = ids[offset : offset + 400]
                placeholders = ",".join("?" * len(chunk))
                await database.execute(
                    f"DELETE FROM upload_tasks WHERE id IN ({placeholders})",
                    chunk,
                )
            await database.commit()

    async def slice_parents_to_recover(self) -> list[int]:
        async with get_db() as database:
            async with database.execute(
                """SELECT id FROM upload_tasks
                   WHERE status = 'oversized' AND parent_id IS NULL
                     AND (
                         slice_parts IS NOT NULL
                         OR error_msg IN (?, ?)
                         OR id IN (SELECT parent_id FROM upload_slices)
                     )
                   ORDER BY id ASC""",
                (MSG_WAIT, MSG_CUTTING),
            ) as cursor:
                return [int(row[0]) for row in await cursor.fetchall()]

    async def latest_child_remote_id(self, parent_id: int) -> int:
        async with get_db() as database:
            async with database.execute(
                """SELECT remote_id FROM upload_tasks
                   WHERE parent_id = ? AND status = 'success' AND remote_id IS NOT NULL
                   ORDER BY part_index DESC LIMIT 1""",
                (parent_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None or row[0] in (None, ""):
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return 0

    async def requeue_slice_child(self, child_id: int) -> bool:
        """失败的分段回到 pending。只动子任务，不把源文件交回机器人。"""
        async with get_db() as database:
            cursor = await database.execute(
                """UPDATE upload_tasks SET status = 'pending', retry_count = 0,
                   assigned_worker = NULL, assigned_at = NULL, started_at = NULL,
                   finished_at = NULL, error_msg = NULL
                   WHERE id = ? AND parent_id IS NOT NULL AND status = 'failed'""",
                (child_id,),
            )
            await database.commit()
            return cursor.rowcount > 0

    async def detach_slice_child(self, child_id: int) -> None:
        """失败分段从看板上清掉之后，计划仍留着，文件也留着，方便以后再入队。"""
        async with get_db() as database:
            await database.execute(
                """UPDATE upload_slices
                   SET status = 'failed', child_task_id = NULL
                   WHERE child_task_id = ?""",
                (child_id,),
            )
            await database.commit()

    async def failed_slice_child_ids(self) -> list[int]:
        async with get_db() as database:
            async with database.execute(
                """SELECT id FROM upload_tasks
                   WHERE status = 'failed' AND parent_id IS NOT NULL
                   ORDER BY id ASC"""
            ) as cursor:
                return [int(row[0]) for row in await cursor.fetchall()]

    async def insert_slice_child(
        self,
        parent: dict,
        *,
        part_index: int,
        part_count: int,
        file_path: str,
        file_name: str,
        file_size: int,
        caption: str,
        status: str,
        after_success: str,
        single_page: int,
        content_page: int,
    ) -> int:
        """插入一段普通上传任务。文件在 data/slices，群和话题沿用源任务。"""
        platform = str(parent.get("platform") or "telegram")
        chat_id = int(parent.get("chat_id") or 0)
        dest_id = parent.get("dest_id") or (str(chat_id) if platform == "telegram" else "")
        async with get_db() as database:
            cursor = await database.execute(
                """INSERT INTO upload_tasks
                (
                   task_id, file_path, file_name, folder_name, file_size, chat_id,
                   caption, single_page, content_page, page_path, status, max_retries,
                   after_success, platform, dest_id, dest_extra, error_msg,
                   parent_id, part_index, part_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    file_path,
                    file_name,
                    parent.get("folder_name"),
                    file_size,
                    chat_id,
                    caption,
                    int(single_page),
                    int(content_page),
                    status,
                    int(parent.get("max_retries") or 3),
                    after_success,
                    platform,
                    dest_id,
                    parent.get("dest_extra"),
                    int(parent["id"]),
                    part_index,
                    part_count,
                ),
            )
            await database.commit()
            child_id = cursor.lastrowid
            if child_id is None:
                raise RuntimeError("切片子任务入库失败")
            return int(child_id)
