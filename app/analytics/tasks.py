"""会话标题与生命周期后台任务"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.analytics.agents.manager import AgentManager
from app.analytics.model_factory import create_active_model
from app.analytics.repositories.conversation import ConversationPGRepo
from app.analytics.services.conversation_lifecycle import ConversationLifecycleService
from app.analytics.services.conversation_title import ConversationTitleService
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.config.app_config import cfg
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.runner import run_async
from app.shared.tasks.submission import TaskSubmission


def _submit(name: str, args: list[object], *, queue: str) -> TaskSubmission:
    task = celery_app.send_task(
        name,
        args=args,
        queue=queue,
        routing_key=queue,
    )
    return TaskSubmission(task_id=task.id)


def enqueue_conversation_title(
    user_id: int,
    conversation_id: UUID,
    expected_title: str,
    user_text: str,
) -> TaskSubmission:
    """提交会话标题生成任务"""
    return _submit(
        "dataagent.analytics.generate_conversation_title",
        [user_id, str(conversation_id), expected_title, user_text],
        queue="lightweight",
    )


def enqueue_conversation_deletion(
    user_id: int,
    conversation_id: UUID,
) -> TaskSubmission:
    """提交会话物理资源删除任务"""
    return _submit(
        "dataagent.analytics.delete_conversation_resources",
        [user_id, str(conversation_id)],
        queue="lifecycle",
    )


async def _repair_conversation_titles() -> int:
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    await persistence.init()
    try:
        repository = ConversationPGRepo(persistence.get_store())
        cutoff = datetime.now(UTC) - timedelta(
            seconds=cfg.task_queue.lifecycle_schedule_seconds
        )
        conversations = await repository.list_pending_title_generations(
            cutoff,
            limit=cfg.lifecycle.cleanup_batch_size,
        )
        for conversation in conversations:
            if conversation.title_source is not None:
                enqueue_conversation_title(
                    conversation.user_id,
                    conversation.id,
                    conversation.title,
                    conversation.title_source,
                )
        return len(conversations)
    finally:
        await persistence.close()


@celery_app.task(name="dataagent.analytics.repair_conversation_titles")
def repair_conversation_titles_task() -> dict[str, int]:
    """重新提交丢失或超时的会话标题任务"""
    return {"dispatched_count": run_async(_repair_conversation_titles())}


async def _generate_conversation_title(
    user_id: int,
    conversation_id: UUID,
    expected_title: str,
    user_text: str,
) -> None:
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    await persistence.init()
    try:
        await ConversationTitleService(create_active_model()).generate_and_update(
            ConversationPGRepo(persistence.get_store()),
            user_id,
            conversation_id,
            expected_title,
            user_text,
        )
    finally:
        await persistence.close()


@celery_app.task(
    name="dataagent.analytics.generate_conversation_title",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def generate_conversation_title_task(
    user_id: int,
    conversation_id: str,
    expected_title: str,
    user_text: str,
) -> dict[str, object]:
    """生成会话标题并进行条件更新"""
    run_async(
        _generate_conversation_title(
            user_id,
            UUID(conversation_id),
            expected_title,
            user_text,
        )
    )
    return {"conversation_id": conversation_id, "updated": True}


async def _run_with_lifecycle_service[T](
    operation: Callable[[ConversationLifecycleService], Awaitable[T]],
) -> T:
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    sandbox = create_sandbox_manager(cfg.sandbox, rebuild_image=False)
    agents = AgentManager(persistence, sandbox)
    service = ConversationLifecycleService(
        persistence,
        agents,
        sandbox,
        cfg.lifecycle,
        session_lock_timeout=cfg.agent.orchestration.session_lock_timeout,
    )
    await persistence.init()
    await sandbox.init(start_cleanup=False)
    try:
        return await operation(service)
    finally:
        await agents.close()
        await sandbox.disconnect()
        await persistence.close()


@celery_app.task(
    name="dataagent.analytics.delete_conversation_resources",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def delete_conversation_resources_task(
    user_id: int,
    conversation_id: str,
) -> dict[str, object]:
    """物理删除会话跨存储资源"""
    identifier = UUID(conversation_id)

    async def operation(service: ConversationLifecycleService) -> bool:
        return await service.delete_conversation_resources(user_id, identifier)

    return {
        "conversation_id": conversation_id,
        "deleted": run_async(_run_with_lifecycle_service(operation)),
    }


@celery_app.task(
    name="dataagent.analytics.cleanup_expired_drafts",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def cleanup_expired_drafts_task() -> dict[str, int]:
    """清理一批过期草稿和已有墓碑的会话"""

    async def operation(service: ConversationLifecycleService) -> tuple[int, int]:
        pending = await service.cleanup_pending_deletions()
        drafts = await service.cleanup_expired_drafts()
        return pending, drafts

    pending_count, draft_count = run_async(_run_with_lifecycle_service(operation))
    return {
        "pending_deleted_count": pending_count,
        "draft_deleted_count": draft_count,
    }
