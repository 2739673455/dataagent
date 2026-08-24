"""后台任务接口模型"""

from typing import Any

from pydantic import BaseModel


class TaskAcceptedResponse(BaseModel):
    """后台任务已受理"""

    task_id: str


class TaskStatusResponse(BaseModel):
    """后台任务执行状态"""

    task_id: str
    state: str
    ready: bool
    successful: bool | None
    result: Any | None = None
    error: str | None = None
