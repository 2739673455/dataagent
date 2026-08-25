"""跨存储用户注销后台任务"""

from datetime import UTC, datetime

from loguru import logger

from app.analytics.agents.manager import AgentManager
from app.analytics.services.conversation_lifecycle import ConversationLifecycleService
from app.identity.repositories.auth import AuthPGRepo
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AuthBase, MetaBase
from app.shared.tasks.celery_app import celery_app
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
    meta_postgres = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    es = ESClientManager(cfg.elasticsearch)
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    sandbox = create_sandbox_manager(cfg.sandbox)
    agents = AgentManager(persistence, sandbox)
    conversations = ConversationLifecycleService(
        persistence,
        agents,
        sandbox,
        cfg.lifecycle,
        session_lock_timeout=cfg.agent.orchestration.session_lock_timeout,
    )
    service = UserDeletionService(
        auth_postgres,
        meta_postgres,
        es,
        sandbox,
        conversations,
        cfg.lifecycle,
    )

    auth_postgres.init()
    meta_postgres.init()
    es.init()
    await persistence.init()
    await sandbox.init(start_cleanup=False)
    try:
        await service.process(user_id)
    finally:
        await agents.close()
        await sandbox.disconnect()
        await persistence.close()
        await es.close()
        await meta_postgres.close()
        await auth_postgres.close()


@celery_app.task(
    name="dataagent.workflows.delete_user",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def delete_user_task(user_id: int) -> dict[str, object]:
    """执行用户跨存储注销清理"""
    logger.info(f"开始执行用户跨存储注销清理: user_id={user_id}")
    run_async(_process_user_deletion(user_id))
    logger.info(f"用户跨存储注销清理完成: user_id={user_id}")
    return {"user_id": user_id, "completed": True}


async def _dispatch_due_user_deletions() -> int:
    """扫描到期注销记录并向生命周期队列提交任务"""
    auth_postgres = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    auth_postgres.init()
    try:
        async with auth_postgres.session() as session:
            tasks = await AuthPGRepo(session).list_due_user_deletions(
                datetime.now(UTC),
                limit=cfg.lifecycle.cleanup_batch_size,
            )
        for task in tasks:
            enqueue_user_deletion(task.user_id)
        logger.info(f"用户注销任务补偿扫描完成: dispatched_count={len(tasks)}")
        return len(tasks)
    finally:
        await auth_postgres.close()


@celery_app.task(name="dataagent.workflows.dispatch_due_user_deletions")
def dispatch_due_user_deletions_task() -> dict[str, int]:
    """提交已到重试时间的用户注销任务"""
    return {"dispatched_count": run_async(_dispatch_due_user_deletions())}
