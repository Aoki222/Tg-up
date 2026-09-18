"""上传进度事件。给日志进度条和 SSE 共用，前端按 JSON 渲染即可。

stage: uploading / success / failed / flood_wait。
单文件 current/total 是字节；相册时 Telethon 可能改成文件序号（含小数）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class UploadProgress:
    task_id: int
    worker_name: str
    file_name: str
    current: float
    total: float
    percent: float
    stage: str  # uploading / success / failed / flood_wait
    message: str = ""
    speed_bps: float = 0.0
    eta_seconds: float = -1.0

    def to_dict(self) -> dict:
        return asdict(self)


def is_album_units(current: float, total: float) -> bool:
    """相册回调里 total 是文件个数（通常很小），不是字节。"""
    return 0 < total <= 32 and current <= total


def make_progress(
    *,
    task_id: int,
    worker_name: str,
    file_name: str,
    current: float,
    total: float,
    stage: str,
    message: str = "",
    speed_bps: float = 0.0,
    eta_seconds: float = -1.0,
) -> UploadProgress:
    percent = 0.0
    if total > 0:
        percent = max(0.0, min(100.0, (current / total) * 100.0))
    if stage in {"success"}:
        percent = 100.0
    return UploadProgress(
        task_id=task_id,
        worker_name=worker_name,
        file_name=file_name,
        current=current,
        total=total,
        percent=round(percent, 1),
        stage=stage,
        message=message,
        speed_bps=speed_bps,
        eta_seconds=eta_seconds,
    )
