"""主流程：发现 → 入库 → 调度 → 发送。阶段只通过任务状态和队列衔接。"""

from .application import UploaderApplication

__all__ = ["UploaderApplication"]
