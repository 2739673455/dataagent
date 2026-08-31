"""用户注销认证状态存储"""

from datetime import datetime

from app.identity import errors as auth_error
from app.identity.repositories.auth import AuthPGRepo
from app.shared.clients.postgres_client_manager import PostgresClientManager


class PostgresUserDeletionStateStore:
    """使用认证 PostgreSQL 原子维护用户注销状态"""

    def __init__(self, postgres: PostgresClientManager) -> None:
        """绑定认证 PostgreSQL 管理器"""
        self._postgres = postgres

    async def request(self, user_id: int, requested_at: datetime) -> bool:
        """禁用用户、吊销令牌并创建注销任务"""
        async with self._postgres.session() as session:
            repo = AuthPGRepo(session)
            async with session.begin():
                await repo.lock_security_mutation()
                user = await repo.get_user_by_id_for_update(user_id)
                task = await repo.get_user_deletion_task_for_update(user_id)
                if user is None:
                    if task is not None and task.status == "completed":
                        return False
                    raise auth_error.UserNotFoundError
                if user.is_active and user.is_admin and await repo.count_admins() <= 1:
                    raise auth_error.LastAdministratorError
                await repo.set_user_active(user, False)
                await repo.revoke_user_refresh_tokens(user.id, requested_at)
                await repo.enqueue_user_deletion(user.id, requested_at)
        return True

    async def is_completed(self, user_id: int) -> bool:
        """判断用户注销任务是否完成"""
        async with self._postgres.session() as session:
            task = await AuthPGRepo(session).get_user_deletion_task(user_id)
            return task is not None and task.status == "completed"

    async def complete(self, user_id: int, completed_at: datetime) -> None:
        """删除认证用户并完成注销任务"""
        async with self._postgres.session() as session:
            repo = AuthPGRepo(session)
            async with session.begin():
                await repo.lock_security_mutation()
                # complete 与失败回写可能来自不同 Worker；行锁保证终态不会被迟到的失败覆盖。
                task = await repo.get_user_deletion_task_for_update(user_id)
                if task is None:
                    raise RuntimeError("用户注销任务记录不存在")
                user = await repo.get_user_by_id_for_update(user_id)
                if user is not None:
                    await repo.delete_user(user)
                await repo.complete_user_deletion(task, completed_at)

    async def record_failure(
        self,
        user_id: int,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        """记录注销失败并安排重试"""
        async with self._postgres.session() as session:
            repo = AuthPGRepo(session)
            async with session.begin():
                task = await repo.get_user_deletion_task_for_update(user_id)
                if task is not None and task.status != "completed":
                    await repo.record_user_deletion_failure(
                        task,
                        error=error,
                        next_attempt_at=next_attempt_at,
                    )

    async def claim_due_user_ids(
        self,
        now: datetime,
        *,
        lease_until: datetime,
        limit: int,
    ) -> list[int]:
        """原子领取已到执行时间的注销用户并设置任务租约"""
        async with self._postgres.session() as session:
            async with session.begin():
                tasks = await AuthPGRepo(session).claim_due_user_deletions(
                    now,
                    lease_until=lease_until,
                    limit=limit,
                )
            return [task.user_id for task in tasks]

    async def extend_claim(self, user_id: int, *, lease_until: datetime) -> bool:
        """在任务开始或重试开始时延长领取租约"""
        async with self._postgres.session() as session, session.begin():
            return await AuthPGRepo(session).extend_user_deletion_claim(
                user_id,
                lease_until=lease_until,
            )
