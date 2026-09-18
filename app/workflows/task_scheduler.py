"""workflows 后台任务提交入口，不加载 Worker 执行资源。"""

from loguru import logger

from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.submission import TaskSubmission


def enqueue_user_deletion(user_id: int) -> TaskSubmission:
    """提交用户注销清理任务。"""
    task = celery_app.send_task(
        "dataagent.workflows.delete_user",
        args=[user_id],
    )
    submission = TaskSubmission(task_id=task.id)
    logger.info(
        f"用户注销清理任务已提交: task_id={submission.task_id}, user_id={user_id}"
    )
    return submission
