"""跨存储用户注销后台任务。"""

from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

from loguru import logger

from app.assistant.conversations.resources import conversation_lifecycle_resources
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.shared.async_runtime import run_async
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AuthBase
from app.shared.tasks.celery_app import celery_app
from app.workflows.task_scheduler import enqueue_user_deletion
from app.workflows.user_deletion import UserDeletionService

# 数据库领取租约覆盖一次任务硬时限和退出余量，与 Broker 可见性分别维护。
USER_DELETION_CLAIM_SECONDS = cfg.task_queue.task_time_limit_seconds + 300


async def _record_failure_safely(
    state_store: PostgresUserDeletionStateStore, user_id: int, error: Exception
) -> None:
    """尽力保存下一次重试时间，不遮蔽原始异常或中断批量投递。"""
    try:
        await state_store.record_failure(
            user_id,
            error=f"{type(error).__name__}: {error}",
            next_attempt_at=datetime.now(UTC)
            + timedelta(seconds=cfg.lifecycle.user_deletion_retry_seconds),
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"回写用户注销失败状态失败，等待租约到期: user_id={user_id}")


async def _process_user_deletion(user_id: int) -> bool:
    """先互斥检查任务，再初始化清理资源；失败由数据库安排重试。"""
    auth_postgres = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    state_store = PostgresUserDeletionStateStore(auth_postgres)
    async with AsyncExitStack() as stack:
        stack.push_async_callback(auth_postgres.close)
        try:
            auth_postgres.init()
            acquired = await stack.enter_async_context(
                state_store.execution_lock(user_id)
            )
            if not acquired:
                return False
            if not await state_store.extend_claim(
                user_id,
                lease_until=datetime.now(UTC)
                + timedelta(seconds=USER_DELETION_CLAIM_SECONDS),
            ):
                return False
            async with conversation_lifecycle_resources() as resources:
                await UserDeletionService(
                    state_store, resources.sandbox, resources.conversations
                ).process(user_id)
            return True
        except Exception as exc:
            # 若已取得用户锁，回写完成前仍持有锁，避免迟到失败干扰下一次执行。
            await _record_failure_safely(state_store, user_id, exc)
            logger.exception(f"用户注销执行失败: user_id={user_id}")
            raise


@celery_app.task(name="dataagent.workflows.delete_user")
def delete_user_task(user_id: int) -> dict[str, object]:
    """执行注销或跳过重复消息；业务重试统一由数据库调度。"""
    processed = run_async(_process_user_deletion(user_id))
    return {"user_id": user_id, "processed": processed}


async def _dispatch_due_user_deletions() -> int:
    """原子领取到期注销记录并向生命周期队列提交任务。"""
    auth_postgres = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    try:
        auth_postgres.init()
        state_store = PostgresUserDeletionStateStore(auth_postgres)
        claimed_at = datetime.now(UTC)
        user_ids = await state_store.claim_due_user_ids(
            claimed_at,
            lease_until=claimed_at + timedelta(seconds=USER_DELETION_CLAIM_SECONDS),
            limit=cfg.lifecycle.cleanup_batch_size,
        )
        dispatched_count = 0
        failed_count = 0
        for user_id in user_ids:
            try:
                enqueue_user_deletion(user_id)
            except Exception as exc:  # noqa: BLE001
                await _record_failure_safely(state_store, user_id, exc)
                failed_count += 1
                logger.exception(f"提交用户注销任务失败: user_id={user_id}")
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
    """提交已到重试时间的用户注销任务。"""
    return {"dispatched_count": run_async(_dispatch_due_user_deletions())}
