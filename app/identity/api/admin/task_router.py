"""管理员后台任务状态查询接口"""

from celery.result import AsyncResult
from fastapi import APIRouter

from app.identity.api.auth.dependencies import AdminUserDep
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.schemas import TaskStatusResponse

router = APIRouter(tags=["tasks"])


@router.get("/{task_id}")
async def get_task_status(
    task_id: str,
    _: AdminUserDep,
) -> TaskStatusResponse:
    """查询后台任务状态和结果"""
    task = AsyncResult(task_id, app=celery_app)
    result = task.result if task.successful() else None
    error = str(task.result) if task.failed() else None
    return TaskStatusResponse(
        task_id=task_id,
        state=task.state,
        ready=task.ready(),
        successful=task.successful() if task.ready() else None,
        result=result,
        error=error,
    )
