"""领域模型：任务、状态、上传策略。"""

from .progress import UploadProgress, make_progress
from .task import AfterSuccess, Task, TaskArtifacts, TaskDestination, TaskPolicy, TaskStatus, task_from_row
from .upload_settings import PreviewMode, UploadSettings

__all__ = [
    "AfterSuccess",
    "UploadProgress",
    "make_progress",
    "PreviewMode",
    "Task",
    "TaskArtifacts",
    "TaskDestination",
    "TaskPolicy",
    "TaskStatus",
    "UploadSettings",
    "task_from_row",
]
