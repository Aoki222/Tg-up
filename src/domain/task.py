from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class TaskStatus(StrEnum):
    PREPARING = "preparing"
    PENDING = "pending"
    ASSIGNED = "assigned"
    UPLOADING = "uploading"
    SUCCESS = "success"
    FAILED = "failed"


def status_from_row(value: object) -> TaskStatus:
    try:
        return TaskStatus(str(value))
    except ValueError:
        return TaskStatus.PENDING


class AfterSuccess(StrEnum):
    KEEP = "keep"
    DELETE = "delete"
    MOVE_TO_ARCHIVE = "move_to_archive"


@dataclass(frozen=True)
class TaskDestination:
    chat_id: int
    topic_id: int | None = None


@dataclass(frozen=True)
class TaskArtifacts:
    video_path: str
    page_path: str | None = None


@dataclass(frozen=True)
class TaskPolicy:
    """入库时从当时的上传配置拷贝，之后热更新不影响这条任务。"""

    need_preview: bool
    after_success: AfterSuccess
    max_retries: int


@dataclass(frozen=True)
class Task:
    id: int
    file_path: str
    file_name: str
    file_size: int
    destination: TaskDestination
    artifacts: TaskArtifacts
    policy: TaskPolicy
    status: TaskStatus = TaskStatus.PENDING
    folder_name: str | None = None
    caption: str = ""
    assigned_worker: str | None = None
    retry_count: int = 0
    error: str | None = None
    single_page: bool = False
    content_page: bool = False

    def merge_row(self, row: dict) -> Task:
        """用数据库最新行刷新可变字段，保留入库时拍下的 policy。"""
        topic_id = row.get("topic_id")
        return replace(
            self,
            file_path=row["file_path"],
            file_name=row.get("file_name") or self.file_name,
            file_size=int(row.get("file_size") or self.file_size),
            folder_name=row.get("folder_name"),
            caption=row.get("caption") or "",
            destination=TaskDestination(
                chat_id=int(row["chat_id"]),
                topic_id=int(topic_id) if topic_id is not None else None,
            ),
            artifacts=TaskArtifacts(
                video_path=row["file_path"],
                page_path=row.get("page_path") or None,
            ),
            status=status_from_row(row["status"]) if row.get("status") else self.status,
            assigned_worker=row.get("assigned_bot"),
            retry_count=int(row.get("retry_count") or 0),
            error=row.get("error_msg"),
            single_page=bool(row.get("single_page")),
            content_page=bool(row.get("content_page")),
        )


def task_from_row(row: dict, policy: TaskPolicy) -> Task:
    topic_id = row.get("topic_id")
    return Task(
        id=int(row["id"]),
        file_path=row["file_path"],
        file_name=row.get("file_name") or "",
        file_size=int(row.get("file_size") or 0),
        folder_name=row.get("folder_name"),
        caption=row.get("caption") or "",
        destination=TaskDestination(
            chat_id=int(row["chat_id"]),
            topic_id=int(topic_id) if topic_id is not None else None,
        ),
        artifacts=TaskArtifacts(
            video_path=row["file_path"],
            page_path=row.get("page_path") or None,
        ),
        policy=policy,
        status=status_from_row(row.get("status")),
        assigned_worker=row.get("assigned_bot"),
        retry_count=int(row.get("retry_count") or 0),
        error=row.get("error_msg"),
        single_page=bool(row.get("single_page")),
        content_page=bool(row.get("content_page")),
    )
