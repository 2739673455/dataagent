"""跨存储用户注销后台任务"""

from datetime import UTC, datetime, timedelta

from loguru import logger

from app.analytics.agents.manager import AgentManager
from app.analytics.agents.skills import packaged_agent_skill_mounts
from app.analytics.providers import build_conversation_lifecycle_service
from app.analytics.services.conversation_tombstone import (
    ConversationTombstoneService,
)
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AnalyticsBase, AuthBase, MetaBase
from app.shared.tasks.celery_app import (
    TASK_VISIBILITY_TIMEOUT_SECONDS,
    celery_app,
)
from app.shared.tasks.runner import run_async
from app.shared.tasks.submission import TaskSubmission
from app.workflows.user_deletion import UserDeletionService


def enqueue_user_deletion(user_id: int) -> TaskSubmission:
    """提交用户注销清理任务"""
    task = celery_app.send_task(
        "dataagent.workflows.delete_user",
        args=[user_id],
        queue="lifecycle",
        routing_key="lifecycle",
    )
    submission = TaskSubmission(task_id=task.id)
    logger.info(
        f"用户注销清理任务已提交: task_id={submission.task_id}, user_id={user_id}"
    )
    return submission


async def _process_user_deletion(user_id: int) -> None:
    """初始化跨存储资源并处理单个用户注销任务"""
    auth_postgres = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    analytics_postgres = PostgresClientManager(
        cfg.langgraph_postgresql,
        AnalyticsBase,
    )
    meta_postgres = PostgresClientManager(
        cfg.meta_postgresql,
        MetaBase,
    )
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    sandbox = create_sandbox_manager(
        cfg.sandbox,
        packaged_agent_skill_mounts(),
    )
    agents = AgentManager(
        persistence,
        sandbox,
        ConversationTombstoneService(analytics_postgres),
    )
    conversations = build_conversation_lifecycle_service(
        persistence,
        analytics_postgres,
        meta_postgres,
        agents,
        sandbox,
        cfg.lifecycle,
    )
    state_store = PostgresUserDeletionStateStore(auth_postgres)
    service = UserDeletionService(
        state_store,
        sandbox,
        conversations,
        cfg.lifecycle,
    )

    auth_postgres.init()
    analytics_postgres.init()
    meta_postgres.init()
    await persistence.init()
    await sandbox.init(start_cleanup=False)
    try:
        started_at = datetime.now(UTC)
        await state_store.extend_claim(
            user_id,
            lease_until=started_at
            + timedelta(seconds=TASK_VISIBILITY_TIMEOUT_SECONDS),
        )
        await service.process(user_id)
    finally:
        await agents.close()
        await sandbox.disconnect()
        await persistence.close()
        await meta_postgres.close()
        await analytics_postgres.close()
        await auth_postgres.close()


@celery_app.task(
    name="dataagent.workflows.delete_user",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def delete_user_task(user_id: int) -> dict[str, object]:
    """执行用户跨存储注销清理"""
    logger.info(f"开始执行用户跨存储注销清理: user_id={user_id}")
    run_async(_process_user_deletion(user_id))
    logger.info(f"用户跨存储注销清理完成: user_id={user_id}")
    return {"user_id": user_id, "completed": True}


async def _dispatch_due_user_deletions() -> int:
    """原子领取到期注销记录并向生命周期队列提交任务"""
    auth_postgres = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    auth_postgres.init()
    try:
        state_store = PostgresUserDeletionStateStore(auth_postgres)
        claimed_at = datetime.now(UTC)
        user_ids = await state_store.claim_due_user_ids(
            claimed_at,
            lease_until=claimed_at
            + timedelta(seconds=TASK_VISIBILITY_TIMEOUT_SECONDS),
            limit=cfg.lifecycle.cleanup_batch_size,
        )
        dispatched_count = 0
        failed_count = 0
        for user_id in user_ids:
            try:
                enqueue_user_deletion(user_id)
            except Exception as exc:  # noqa: BLE001
                failed_at = datetime.now(UTC)
                await state_store.record_failure(
                    user_id,
                    error=f"{type(exc).__name__}: {exc}",
                    next_attempt_at=failed_at
                    + timedelta(seconds=cfg.lifecycle.user_deletion_retry_seconds),
                )
                failed_count += 1
                logger.exception(
                    f"提交用户注销任务失败并释放领取: user_id={user_id}"
                )
            else:
                dispatched_count += 1
        logger.info(
            "用户注销任务调度完成: "
            f"claimed_count={len(user_ids)}, dispatched_count={dispatched_count}, "
            f"failed_count={failed_count}"
        )
        return dispatched_count
    finally:
        await auth_postgres.close()


@celery_app.task(name="dataagent.workflows.dispatch_due_user_deletions")
def dispatch_due_user_deletions_task() -> dict[str, int]:
    """提交已到重试时间的用户注销任务"""
    return {"dispatched_count": run_async(_dispatch_due_user_deletions())}
