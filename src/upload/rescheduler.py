from typing import Protocol

class Rescheduler(Protocol):
    def request_reschedule(self) -> None:
        """请求调度器立刻跑一次，多次调用可合并。"""
        ...