"""后台任务提交结果"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    """已提交的 Celery 任务"""

    task_id: str
