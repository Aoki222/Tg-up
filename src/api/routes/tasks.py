"""看板、成功记录、重试、清除、切片和进度推送。SQL 留在 TaskRepository。"""

from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ...adapters.progress import ProgressHub
from ...domain.slices import MSG_TOO_DENSE, minimum_slice_parts
from ..app_auth import require_token
from ..models import FailedIdsBody, OversizedIdsBody, SliceBody
from ..present import _board_item, _sse


def register(app: FastAPI) -> None:
    # 没有命中路由、因此没有进入上传队列的文件。
    @app.get("/api/unmatched")
    async def list_unmatched() -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        rows = await repo.list_unmatched()
        return {"items": rows, "count": len(rows)}

    # 上传成功的分页列表。条件和顶部今日/累计数字一致。
    @app.get("/api/tasks/success")
    async def list_success(
        scope: str = Query(default="all"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
    ) -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        if scope not in {"today", "all"}:
            raise HTTPException(status_code=400, detail="scope 只能是 today 或 all")
        rows, total = await repo.list_success_page(scope=scope, page=page, page_size=page_size)
        items = []
        for row in rows:
            part_index = row.get("part_index")
            part_count = row.get("part_count")
            part_label = None
            if part_index and part_count:
                part_label = f"第 {int(part_index)}/{int(part_count)} 段"
            items.append(
                {
                    "id": int(row["id"]),
                    "file_name": row.get("file_name") or "",
                    "file_size": int(row.get("file_size") or 0),
                    "folder_name": row.get("folder_name"),
                    "finished_at": row.get("finished_at"),
                    "part_label": part_label,
                }
            )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # 看板快照：进行中的任务，外加最近失败。上传中的行叠上实时进度。
    @app.get("/api/tasks")
    async def list_tasks() -> dict:
        """看板快照：SQLite 任务行叠上 ProgressHub 的实时字节/速度。"""
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        hub: ProgressHub = app.state.progress_hub
        rows = await repo.list_board_tasks()
        counts = await repo.count_board_statuses()
        latest = {item.task_id: item for item in hub.snapshot()}
        items = [_board_item(row, latest.get(int(row["id"]))) for row in rows]
        return {"items": items, "counts": counts}

    def _wake_scheduler() -> None:
        wake = app.state.reschedule
        if wake is not None:
            wake()

    # 把库里全部 failed 重置为 pending，次数归零。缺文件的跳过。
    @app.post("/api/tasks/retry-failed", dependencies=[Depends(require_token)])
    async def retry_all_failed() -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        retried, skipped, parked = await repo.requeue_all_failed()
        slicer = app.state.slice_service
        if slicer is not None:
            await slicer.sync_requeued_parts()
        if retried:
            _wake_scheduler()
        return {"ok": True, "retried": retried, "skipped": skipped, "parked": parked}

    # 重试一条失败任务：failed → pending，retry_count 归零。
    @app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(require_token)])
    async def retry_task(task_id: int) -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        result = await repo.requeue_failed(task_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="找不到任务")
        if result == "not_failed":
            raise HTTPException(status_code=409, detail="只能重试失败任务")
        if result == "missing_file":
            raise HTTPException(status_code=409, detail="文件不存在")
        slicer = app.state.slice_service
        if slicer is not None:
            await slicer.sync_requeued_parts()
        fresh = await repo.get_task_by_id(task_id)
        status = str(fresh.get("status") if fresh else "pending")
        if status == "pending":
            _wake_scheduler()
        return {"ok": True, "id": task_id, "status": status, "retry_count": 0}

    # 过大文件只交给个人号。没有可用个人号时明确拒绝，不回退到 Bot。
    @app.post("/api/tasks/{task_id}/dispatch-user", dependencies=[Depends(require_token)])
    async def dispatch_oversized(task_id: int) -> dict:
        dispatch = app.state.dispatch_oversized
        if dispatch is None:
            raise HTTPException(status_code=503, detail="任务分发未就绪")
        result = await dispatch(task_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="找不到任务")
        if result == "not_oversized":
            raise HTTPException(status_code=409, detail="只能把过大文件交给个人号")
        if result == "missing_file":
            raise HTTPException(status_code=409, detail="文件不存在")
        if result == "no_user":
            raise HTTPException(status_code=409, detail="没有可用的个人账号")
        if result == "slicing":
            raise HTTPException(status_code=409, detail="正在切片，请先清除")
        if result != "ok":
            raise HTTPException(status_code=503, detail="任务分发未就绪")
        return {"ok": True, "id": task_id, "status": "assigned"}

    # 过大视频按用户指定的段数切片，再逐段进入现有上传队列。
    @app.post("/api/tasks/{task_id}/slice", dependencies=[Depends(require_token)])
    async def slice_task(task_id: int, body: SliceBody) -> dict:
        slicer = app.state.slice_service
        if slicer is None:
            raise HTTPException(status_code=503, detail="切片未就绪")
        result = await slicer.request(task_id, body.parts)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="找不到任务")
        if result == "not_oversized":
            raise HTTPException(status_code=409, detail="只能切片过大的文件")
        if result == "not_video":
            raise HTTPException(status_code=409, detail="这个文件不能切片")
        if result == "missing_file":
            raise HTTPException(status_code=409, detail="文件不存在")
        if result == "busy":
            raise HTTPException(status_code=409, detail="已在切片")
        if result.startswith("bad_parts:"):
            minimum = result.split(":", 1)[1]
            raise HTTPException(status_code=409, detail=f"段数至少为 {minimum}，最多 30")
        if result != "ok":
            raise HTTPException(status_code=503, detail="切片未就绪")
        return {"ok": True, "id": task_id, "parts": body.parts}

    # 某一段上传失败后，从那段接着传，不重新切片。
    @app.post("/api/tasks/{task_id}/slice-continue", dependencies=[Depends(require_token)])
    async def continue_slice(task_id: int) -> dict:
        slicer = app.state.slice_service
        if slicer is None:
            raise HTTPException(status_code=503, detail="切片未就绪")
        result = await slicer.continue_upload(task_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="找不到任务")
        if result == "not_oversized":
            raise HTTPException(status_code=409, detail="只能继续过大文件的切片")
        if result == "not_failed":
            raise HTTPException(status_code=409, detail="没有失败的切片")
        if result == "missing_file":
            raise HTTPException(status_code=409, detail="切片文件不存在")
        if result != "ok":
            raise HTTPException(status_code=503, detail="切片未就绪")
        return {"ok": True, "id": task_id}

    def _skip(row: dict | None, task_id: int, reason: str) -> dict:
        name = ""
        if row is not None:
            name = str(row.get("file_name") or "")
        return {"id": task_id, "file_name": name, "reason": reason}

    def _slice_skip_reason(result: str) -> str:
        if result == "not_video":
            return "这个文件不能切片"
        if result == "missing_file":
            return "文件不存在"
        if result == "busy":
            return "已在切片"
        if result == "not_oversized":
            return "不是过大文件"
        if result == "not_found":
            return "找不到任务"
        if result.startswith("bad_parts:"):
            minimum = result.split(":", 1)[1]
            if minimum.isdigit() and int(minimum) > 30:
                return MSG_TOO_DENSE
            return f"段数至少为 {minimum}，最多 30"
        return "切片未就绪"

    async def _oversized_rows(repo, ids: list[int]) -> list[dict]:
        if not ids:
            wanted = await repo.list_oversized_root_ids()
        else:
            wanted = [int(task_id) for task_id in ids if int(task_id) > 0]
        rows: list[dict] = []
        for task_id in wanted:
            row = await repo.get_task_by_id(task_id)
            if row is None or str(row.get("status")) != "oversized" or row.get("parent_id"):
                continue
            rows.append(row)
        return rows

    # 选中的或全部过大视频按各自最少段数进入切片队列。
    @app.post("/api/tasks/oversized/slice", dependencies=[Depends(require_token)])
    async def slice_oversized_batch(payload: OversizedIdsBody) -> dict:
        repo = app.state.task_repository
        slicer = app.state.slice_service
        if repo is None or slicer is None:
            raise HTTPException(status_code=503, detail="切片未就绪")
        queued = 0
        skipped: list[dict] = []
        for row in await _oversized_rows(repo, payload.ids):
            task_id = int(row["id"])
            parts = minimum_slice_parts(int(row.get("file_size") or 0))
            result = await slicer.request(task_id, parts)
            if result == "ok":
                queued += 1
                continue
            skipped.append(_skip(row, task_id, _slice_skip_reason(result)))
        return {"ok": True, "queued": queued, "skipped": skipped}

    # 选中的或全部过大文件交给个人号。没有个人号时一条都不分配。
    @app.post("/api/tasks/oversized/dispatch-user", dependencies=[Depends(require_token)])
    async def dispatch_oversized_batch(payload: OversizedIdsBody) -> dict:
        repo = app.state.task_repository
        dispatch = app.state.dispatch_oversized
        if repo is None or dispatch is None:
            raise HTTPException(status_code=503, detail="任务分发未就绪")
        assigned = 0
        skipped: list[dict] = []
        for row in await _oversized_rows(repo, payload.ids):
            task_id = int(row["id"])
            result = await dispatch(task_id)
            if result == "ok":
                assigned += 1
                continue
            if result == "no_user" and assigned == 0:
                raise HTTPException(status_code=409, detail="没有可用的个人账号")
            reason = {
                "no_user": "没有可用的个人账号",
                "slicing": "正在切片，请先清除",
                "missing_file": "文件不存在",
                "not_oversized": "不是过大文件",
                "not_found": "找不到任务",
            }.get(result, "无法交给个人号")
            skipped.append(_skip(row, task_id, reason))
            if result == "no_user":
                break
        return {"ok": True, "assigned": assigned, "skipped": skipped}

    async def _delete_oversized(ids: list[int] | None) -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        slicer = app.state.slice_service
        rows = await _oversized_rows(repo, ids or [])
        deleted = 0
        skipped: list[dict] = []
        removed: list[int] = []
        for row in rows:
            task_id = int(row["id"])
            child_ids: list[int] = []
            if slicer is not None:
                gate, child_ids = await slicer.discard(task_id)
                if gate == "uploading":
                    skipped.append(_skip(row, task_id, "有分段正在上传"))
                    continue
            result = await repo.delete_failed(task_id)
            if result != "ok":
                skipped.append(_skip(row, task_id, "不是过大文件"))
                continue
            deleted += 1
            removed.extend(child_ids)
            removed.append(task_id)
        _forget_progress(removed)
        return {"ok": True, "deleted": deleted, "skipped": skipped}

    @app.post("/api/tasks/oversized/delete", dependencies=[Depends(require_token)])
    async def delete_selected_oversized(payload: OversizedIdsBody) -> dict:
        if not payload.ids:
            return {"ok": True, "deleted": 0, "skipped": []}
        return await _delete_oversized(payload.ids)

    @app.delete("/api/tasks/oversized", dependencies=[Depends(require_token)])
    async def delete_all_oversized() -> dict:
        return await _delete_oversized(None)

    def _forget_progress(task_ids: list[int]) -> None:
        hub = app.state.progress_hub
        if hub is not None and task_ids:
            hub.forget(task_ids)

    # 删除全部失败记录。不删磁盘上的文件。
    @app.delete("/api/tasks/failed", dependencies=[Depends(require_token)])
    async def delete_all_failed() -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        slicer = app.state.slice_service
        part_ids = await repo.failed_slice_child_ids() if slicer is not None else []
        root_ids = await repo.list_failed_root_ids()
        cleared = await slicer.clear_failed_parts() if slicer is not None else 0
        deleted = await repo.delete_all_failed()
        _forget_progress([*part_ids, *root_ids])
        return {"ok": True, "deleted": deleted + cleared}

    # 按 id 删除选中的失败记录。不是 failed 的行会跳过。
    @app.post("/api/tasks/failed/delete", dependencies=[Depends(require_token)])
    async def delete_selected_failed(payload: FailedIdsBody) -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        slicer = app.state.slice_service
        child_ids: list[int] = []
        rest: list[int] = []
        for task_id in payload.ids:
            row = await repo.get_task_by_id(int(task_id))
            if row is None:
                continue
            status = str(row.get("status"))
            if row.get("parent_id") and status == "failed":
                child_ids.append(int(task_id))
            elif status == "failed":
                rest.append(int(task_id))
        cleared = await slicer.clear_failed_parts(child_ids) if slicer is not None else 0
        deleted = await repo.delete_failed_ids(rest)
        removed = [*child_ids, *rest] if slicer is not None else rest
        _forget_progress(removed)
        return {"ok": True, "deleted": deleted + cleared}

    # 删除一条失败记录。进行中的任务不能删。
    @app.delete("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
    async def delete_task(task_id: int) -> dict:
        repo = app.state.task_repository
        if repo is None:
            raise HTTPException(status_code=503, detail="任务仓库未就绪")
        row = await repo.get_task_by_id(task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到任务")
        slicer = app.state.slice_service
        removed: list[int] = []
        if row.get("parent_id"):
            if str(row.get("status")) != "failed":
                raise HTTPException(status_code=409, detail="只能清除失败或过大的任务")
            if slicer is None or not await slicer.clear_failed_part(task_id):
                raise HTTPException(status_code=409, detail="只能清除失败或过大的任务")
            _forget_progress([task_id])
            return {"ok": True, "id": task_id, "deleted": True}
        if str(row.get("status")) == "oversized" and slicer is not None:
            gate, child_ids = await slicer.discard(task_id)
            if gate == "uploading":
                raise HTTPException(status_code=409, detail="有分段正在上传")
            removed.extend(child_ids)
        result = await repo.delete_failed(task_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="找不到任务")
        if result == "not_failed":
            raise HTTPException(status_code=409, detail="只能清除失败或过大的任务")
        removed.append(task_id)
        _forget_progress(removed)
        return {"ok": True, "id": task_id, "deleted": True}

    # 当前仍在 uploading 的进度快照，含服务端算好的速度。
    @app.get("/api/progress")
    async def progress_snapshot() -> dict:
        hub: ProgressHub = app.state.progress_hub
        return {"items": [item.to_dict() for item in hub.snapshot()]}

    # SSE 进度流。EventSource 不能带头，令牌放查询参数 access_token。
    @app.get("/api/progress/stream")
    async def progress_stream(request: Request) -> StreamingResponse:
        """先推当前快照，再持续推送。空闲时发 keepalive，避免代理掐连接。"""
        hub: ProgressHub = request.app.state.progress_hub
        queue = hub.subscribe()

        async def event_source():
            try:
                for item in hub.snapshot():
                    yield _sse(item)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=20)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield _sse(item)
            finally:
                hub.unsubscribe(queue)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
