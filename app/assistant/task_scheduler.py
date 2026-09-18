"""assistant 后台任务提交入口，不加载 Worker 执行资源。"""

from uuid import UUID

from loguru import logger

from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.submission import TaskSubmission


def _submit(name: str, args: list[object]) -> TaskSubmission:
    """按中央路由配置提交助手后台任务。"""
    task = celery_app.send_task(
        name,
        args=args,
    )
    submission = TaskSubmission(task_id=task.id)
    logger.info(f"助手后台任务已提交: task_id={submission.task_id}, name={name}")
    return submission


def enqueue_conversation_title(
    user_id: int,
    conversation_id: UUID,
    expected_title: str,
    user_text: str,
) -> TaskSubmission:
    """提交会话标题生成任务。"""
    return _submit(
        "dataagent.assistant.generate_conversation_title",
        [user_id, str(conversation_id), expected_title, user_text],
    )


def enqueue_conversation_deletion(
    user_id: int,
    conversation_id: UUID,
) -> TaskSubmission:
    """提交会话物理资源删除任务。"""
    return _submit(
        "dataagent.assistant.delete_conversation_resources",
        [user_id, str(conversation_id)],
    )
