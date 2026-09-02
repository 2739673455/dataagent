"""会话标题与生命周期后台任务。"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from loguru import logger

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.agents.manager import AgentManager
from app.assistant.model_factory import create_configured_model
from app.assistant.providers import build_conversation_lifecycle_service
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.conversation_lifecycle import ConversationLifecycleService
from app.assistant.services.conversation_title import ConversationTitleService
from app.assistant.services.conversation_tombstone_store import (
    ConversationTombstoneStore,
)
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AssistantBase, MetaBase
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.runner import run_async
from app.shared.tasks.submission import TaskSubmission


def _submit(name: str, args: list[object], *, queue: str) -> TaskSubmission:
    """向指定队列提交助手后台任务。"""
    task = celery_app.send_task(
        name,
        args=args,
        queue=queue,
        routing_key=queue,
    )
    submission = TaskSubmission(task_id=task.id)
    logger.info(
        f"助手后台任务已提交: task_id={submission.task_id}, name={name}, queue={queue}"
    )
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
        queue="lightweight",
    )


def enqueue_conversation_deletion(
    user_id: int,
    conversation_id: UUID,
) -> TaskSubmission:
    """提交会话物理资源删除任务。"""
    return _submit(
        "dataagent.assistant.delete_conversation_resources",
        [user_id, str(conversation_id)],
        queue="lifecycle",
    )


async def _generate_conversation_title(
    user_id: int,
    conversation_id: UUID,
    expected_title: str,
    user_text: str,
) -> bool:
    """创建短生命周期资源并生成单个会话标题。"""
    assistant_postgres = PostgresClientManager(
        cfg.langgraph_postgresql,
        AssistantBase,
    )
    assistant_postgres.init()
    try:
        async with assistant_postgres.session() as session:
            updated = await ConversationTitleService(
                create_configured_model(cfg.lm_config.active)
            ).generate_and_update(
                ConversationPGRepo(session),
                user_id,
                conversation_id,
                expected_title,
                user_text,
            )
            await session.commit()
            return updated
    finally:
        await assistant_postgres.close()


@celery_app.task(
    name="dataagent.assistant.generate_conversation_title",
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
    """生成会话标题并进行条件更新。"""
    logger.info(
        f"开始生成会话标题: user_id={user_id}, conversation_id={conversation_id}"
    )
    updated = run_async(
        _generate_conversation_title(
            user_id,
            UUID(conversation_id),
            expected_title,
            user_text,
        )
    )
    logger.info(
        f"会话标题生成完成: user_id={user_id}, conversation_id={conversation_id}"
    )
    return {"conversation_id": conversation_id, "updated": updated}


async def _run_with_lifecycle_service[T](
    operation: Callable[[ConversationLifecycleService], Awaitable[T]],
) -> T:
    """初始化会话生命周期资源并执行指定操作。"""
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    assistant_postgres = PostgresClientManager(
        cfg.langgraph_postgresql,
        AssistantBase,
    )
    meta_postgres = PostgresClientManager(
        cfg.meta_postgresql,
        MetaBase,
    )
    sandbox = create_sandbox_manager(
        cfg.sandbox,
        packaged_skill_readonly_mounts(),
    )
    agents = AgentManager(
        persistence,
        sandbox,
        ConversationTombstoneStore(assistant_postgres),
    )
    service = build_conversation_lifecycle_service(
        persistence,
        assistant_postgres,
        meta_postgres,
        agents,
        sandbox,
        cfg.lifecycle,
    )
    await persistence.init()
    assistant_postgres.init()
    meta_postgres.init()
    await sandbox.init(start_cleanup=False)
    try:
        return await operation(service)
    finally:
        await agents.close()
        await sandbox.disconnect()
        await meta_postgres.close()
        await assistant_postgres.close()
        await persistence.close()


@celery_app.task(
    name="dataagent.assistant.delete_conversation_resources",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def delete_conversation_resources_task(
    user_id: int,
    conversation_id: str,
) -> dict[str, object]:
    """物理删除会话跨存储资源。"""
    identifier = UUID(conversation_id)
    logger.info(
        f"开始删除会话物理资源: user_id={user_id}, conversation_id={conversation_id}"
    )

    async def operation(service: ConversationLifecycleService) -> bool:
        """删除指定会话的全部物理资源。"""
        return await service.delete_conversation_resources(user_id, identifier)

    deleted = run_async(_run_with_lifecycle_service(operation))
    logger.info(
        "会话物理资源删除完成: "
        f"user_id={user_id}, conversation_id={conversation_id}, "
        f"deleted={deleted}"
    )
    return {"conversation_id": conversation_id, "deleted": deleted}


@celery_app.task(
    name="dataagent.assistant.cleanup_expired_drafts",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def cleanup_expired_drafts_task() -> dict[str, int]:
    """清理一批过期草稿和已有墓碑的会话。"""
    logger.info("开始清理过期草稿和待删除会话")

    async def operation(service: ConversationLifecycleService) -> tuple[int, int]:
        """清理待删除会话和过期草稿。"""
        pending = await service.cleanup_pending_deletions()
        drafts = await service.cleanup_expired_drafts()
        return pending, drafts

    pending_count, draft_count = run_async(_run_with_lifecycle_service(operation))
    logger.info(
        "过期草稿和待删除会话清理完成: "
        f"pending_deleted_count={pending_count}, "
        f"draft_deleted_count={draft_count}"
    )
    return {
        "pending_deleted_count": pending_count,
        "draft_deleted_count": draft_count,
    }
