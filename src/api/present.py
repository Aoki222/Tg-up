"""把数据库行和进度快照拼成前端能直接渲染的字典。这里不写 SQL。"""

from __future__ import annotations

import json

from ..domain.progress import UploadProgress
from ..domain.slices import is_sliceable_video, minimum_slice_parts, slice_phase

def _sse(progress: UploadProgress) -> str:
    return f"event: progress\ndata: {json.dumps(progress.to_dict(), ensure_ascii=False)}\n\n"


def _part_number(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _board_item(row: dict, progress: UploadProgress | None) -> dict:
    status = str(row.get("status") or "pending")
    if status == "retrying":
        status = "pending"
    item = {
        "id": int(row["id"]),
        "file_name": row.get("file_name") or "",
        "file_size": int(row.get("file_size") or 0),
        "folder_name": row.get("folder_name"),
        "status": status,
        "assigned_worker": row.get("assigned_worker") or row.get("assigned_bot"),
        "platform": row.get("platform") or "telegram",
        "dest_id": row.get("dest_id") or (str(row["chat_id"]) if row.get("chat_id") is not None else ""),
        "retry_count": int(row.get("retry_count") or 0),
        "max_retries": int(row.get("max_retries") or 3),
        "error": row.get("error_msg"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "percent": 0.0,
        "current": 0.0,
        "total": float(row.get("file_size") or 0),
        "speed_bps": 0.0,
        "eta_seconds": -1.0,
        "stage": None,
        "message": "",
        "sliceable": False,
        "slice_phase": "idle",
        "slice_min_parts": None,
        "part_index": _part_number(row.get("part_index")),
        "part_count": _part_number(row.get("part_count")),
    }
    if status == "oversized":
        sliceable = is_sliceable_video(str(row.get("file_name") or row.get("file_path") or ""))
        item["sliceable"] = sliceable
        item["slice_phase"] = slice_phase(row.get("error_msg"))
        if sliceable:
            item["slice_min_parts"] = minimum_slice_parts(int(row.get("file_size") or 0))
    if progress is not None:
        item["percent"] = progress.percent
        item["current"] = progress.current
        item["total"] = progress.total
        item["speed_bps"] = progress.speed_bps
        item["eta_seconds"] = progress.eta_seconds
        item["stage"] = progress.stage
        item["message"] = progress.message
        if progress.worker_name and not item["assigned_worker"]:
            item["assigned_worker"] = progress.worker_name
    return item


def _resolve_dest_name(platform: str, dest_id: str, settings) -> str:
    if platform == "telegram":
        try:
            cid = int(dest_id)
            for c in settings.chats:
                if c.chat_id == cid:
                    return c.alias.strip() or c.title.strip() or str(cid)
        except (ValueError, TypeError):
            pass
        return dest_id
    elif platform == "gdrive":
        for f in settings.drive_folders:
            if f.folder_id == dest_id:
                return f.name.strip() or dest_id
        return dest_id
    return dest_id
