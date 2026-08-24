"""跨存储用户注销编排"""

from datetime import UTC, datetime, timedelta

from app.analytics.services.conversation_lifecycle import (
    ConversationLifecycleService,
)
from app.identity import errors as auth_error
from app.identity.repositories.auth import AuthPGRepo
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import LifecycleConfig


class UserDeletionService:
    """协调认证库、会话库、元数据库、索引和沙盒的用户注销"""

    def __init__(
        self,
        auth_postgres: PostgresClientManager,
        meta_postgres: PostgresClientManager,
        es: ESClientManager,
        sandbox: DockerSandboxManager,
        conversations: ConversationLifecycleService,
        config: LifecycleConfig,
    ) -> None:
        self._auth_postgres = auth_postgres
        self._meta_postgres = meta_postgres
        self._es = es
        self._sandbox = sandbox
        self._conversations = conversations
        self._config = config

    async def request_deletion(self, user_id: int, *, operator_id: int) -> bool:
        """禁用目标用户并持久化注销任务"""
        if user_id == operator_id:
            raise auth_error.InvalidUserMutationError(
                detail="不能注销当前操作的管理员账号"
            )

        now = datetime.now(UTC)
        async with self._auth_postgres.session() as session:
            repo = AuthPGRepo(session)
            async with session.begin():
                await repo.lock_security_mutation()
                user = await repo.get_user_by_id_for_update(user_id)
                task = await repo.get_user_deletion_task(user_id)
                if user is None:
                    if task is not None and task.status == "completed":
                        return False
                    raise auth_error.UserNotFoundError
                if user.is_active and user.is_admin and await repo.count_admins() <= 1:
                    raise auth_error.LastAdministratorError
                await repo.set_user_active(user, False)
                await repo.revoke_user_refresh_tokens(user.id, now)
                await repo.enqueue_user_deletion(user.id, now)
        return True

    async def process(self, user_id: int) -> None:
        """幂等执行一个用户的跨存储注销清理"""
        if await self._is_completed(user_id):
            return
        try:
            await self._conversations.delete_user_conversations(user_id)
            await self._sandbox.delete_user_sandbox(user_id)
            await self._delete_query_history(user_id)
            await self._complete(user_id)
        except Exception as exc:
            await self._record_failure(user_id, exc)
            raise

    async def _is_completed(self, user_id: int) -> bool:
        async with self._auth_postgres.session() as session:
            task = await AuthPGRepo(session).get_user_deletion_task(user_id)
            return task is not None and task.status == "completed"

    async def _delete_query_history(self, user_id: int) -> None:
        async with self._meta_postgres.session() as session:
            repo = QueryExperiencePGRepo(session)
            async with session.begin():
                experience_ids = await repo.list_ids_by_user(user_id)

        await QueryExperienceESRepo(self._es.get_client()).delete_many(experience_ids)

        async with self._meta_postgres.session() as session:
            repo = QueryExperiencePGRepo(session)
            async with session.begin():
                await repo.delete_by_user(user_id)

    async def _complete(self, user_id: int) -> None:
        now = datetime.now(UTC)
        async with self._auth_postgres.session() as session:
            repo = AuthPGRepo(session)
            async with session.begin():
                await repo.lock_security_mutation()
                task = await repo.get_user_deletion_task(user_id)
                if task is None:
                    raise RuntimeError("用户注销任务记录不存在")
                user = await repo.get_user_by_id_for_update(user_id)
                if user is not None:
                    await repo.delete_user(user)
                await repo.complete_user_deletion(task, now)

    async def _record_failure(self, user_id: int, exc: Exception) -> None:
        now = datetime.now(UTC)
        next_attempt_at = now + timedelta(
            seconds=self._config.user_deletion_retry_seconds
        )
        async with self._auth_postgres.session() as session:
            repo = AuthPGRepo(session)
            async with session.begin():
                task = await repo.get_user_deletion_task(user_id)
                if task is not None and task.status != "completed":
                    await repo.record_user_deletion_failure(
                        task,
                        error=f"{type(exc).__name__}: {exc}",
                        next_attempt_at=next_attempt_at,
                    )

    async def list_due_user_ids(self) -> list[int]:
        """列出到达重试时间的用户注销任务"""
        async with self._auth_postgres.session() as session:
            tasks = await AuthPGRepo(session).list_due_user_deletions(
                datetime.now(UTC),
                limit=self._config.cleanup_batch_size,
            )
            return [task.user_id for task in tasks]
