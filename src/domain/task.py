"""任务是领域对象，不是「一行 SQL 的别名」。

destination / artifacts / policy / runtime 只是代码里的分组，落到 SQLite
仍是一列一个标量，不要把多个值拼进同一个字段。

状态只允许：
  preparing → pending → assigned → uploading → success
                              ↘ 失败未超次数回到 pending
                              ↘ 超限 → failed
没有 retrying。库里若还有旧的 retrying，读出来当 pending。

policy 在入库那一刻从 upload.toml 拷贝。之后热更新只影响新任务。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class TaskStatus(StrEnum):
    # preparing → pending → assigned → uploading → success | failed
    # 失败未超次数回到 pending；没有 retrying 这种多余状态
    PREPARING = "preparing"
    PENDING = "pending"
    ASSIGNED = "assigned"
    UPLOADING = "uploading"
    SUCCESS = "success"
    FAILED = "failed"


def status_from_row(value: object) -> TaskStatus:
    """无法识别的旧状态（例如 retrying）一律当成 pending，避免调度崩溃。"""
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
    # 论坛话题必须带 topic_id；普通群为 None
    chat_id: int
    topic_id: int | None = None


@dataclass(frozen=True)
class TaskArtifacts:
    video_path: str
    page_path: str | None = None  # 封面；预览失败则为 None，只发视频


@dataclass(frozen=True)
class TaskPolicy:
    """入库时从当时的上传配置拷贝，之后热更新不影响这条任务。"""

    need_preview: bool
    after_success: AfterSuccess
    max_retries: int


@dataclass(frozen=True)
class Task:
    """领域对象。destination/artifacts/policy 是分组，不是一个数据库字段。"""

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
    """把数据库行装配成 Task。policy 必须由调用方传入（内存快照或当前配置）。"""
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
