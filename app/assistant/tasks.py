"""会话标题与生命周期后台任务。"""

from uuid import UUID

from loguru import logger

from app.assistant.lifecycle_runtime import conversation_lifecycle_resources
from app.assistant.model_factory import create_configured_model
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.conversation_title import ConversationTitleService
from app.shared.async_runtime import run_async
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AssistantBase
from app.shared.tasks.celery_app import celery_app


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
    try:
        assistant_postgres.init()
        async with (
            create_configured_model(cfg.lm_config.active) as model,
            assistant_postgres.session() as session,
        ):
            updated = await ConversationTitleService(model).generate_and_update(
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

    async def operation() -> bool:
        """删除指定会话的全部物理资源。"""
        async with conversation_lifecycle_resources() as resources:
            return await resources.conversations.delete_conversation_resources(
                user_id, identifier
            )

    deleted = run_async(operation())
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

    async def operation() -> tuple[int, int]:
        """清理待删除会话和过期草稿。"""
        async with conversation_lifecycle_resources() as resources:
            pending = await resources.conversations.cleanup_pending_deletions()
            drafts = await resources.conversations.cleanup_expired_drafts()
            return pending, drafts

    pending_count, draft_count = run_async(operation())
    logger.info(
        "过期草稿和待删除会话清理完成: "
        f"pending_deleted_count={pending_count}, "
        f"draft_deleted_count={draft_count}"
    )
    return {
        "pending_deleted_count": pending_count,
        "draft_deleted_count": draft_count,
    }
