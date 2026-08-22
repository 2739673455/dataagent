"""跨存储用户注销编排"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

from loguru import logger

from app.clients.docker_sandbox_manager import (
    DockerSandboxManager,
    docker_sandbox_manager,
)
from app.clients.es_client_manager import ESClientManager, es_client_manager
from app.clients.postgres_client_manager import (
    PostgresClientManager,
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.conf.app_config import LifecycleConfig, cfg
from app.errors import auth_error
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.query_experience_es_repo import QueryExperienceESRepo
from app.repositories.query_experience_pg_repo import QueryExperiencePGRepo
from app.services.conversation_lifecycle_service import (
    ConversationLifecycleService,
    conversation_lifecycle_service,
)


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
        self._locks: dict[int, asyncio.Lock] = {}
        self._lock_ref_counts: dict[int, int] = {}
        self._locks_guard = asyncio.Lock()
        self._worker_task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()

    async def request_deletion(self, user_id: int, *, operator_id: int) -> None:
        """禁用目标用户并立即尝试执行持久化注销任务"""
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
                        return
                    raise auth_error.UserNotFoundError
                if (
                    user.is_active
                    and user.is_admin
                    and await repo.count_admins() <= 1
                ):
                    raise auth_error.LastAdministratorError
                await repo.set_user_active(user, False)
                await repo.revoke_user_refresh_tokens(user.id, now)
                await repo.enqueue_user_deletion(user.id, now)

        self._wake_event.set()
        try:
            await self._process(user_id)
        except Exception as exc:
            raise auth_error.UserDeletionPendingError(
                detail="用户注销清理已加入自动重试队列"
            ) from exc

    async def start(self) -> None:
        """启动用户注销任务重试协程"""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def close(self) -> None:
        """停止用户注销任务重试协程"""
        task = self._worker_task
        self._worker_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @contextlib.asynccontextmanager
    async def _user_lock(self, user_id: int) -> AsyncGenerator[None]:
        async with self._locks_guard:
            lock = self._locks.setdefault(user_id, asyncio.Lock())
            self._lock_ref_counts[user_id] = (
                self._lock_ref_counts.get(user_id, 0) + 1
            )
        try:
            async with lock:
                yield
        finally:
            async with self._locks_guard:
                remaining = self._lock_ref_counts[user_id] - 1
                if remaining:
                    self._lock_ref_counts[user_id] = remaining
                else:
                    self._lock_ref_counts.pop(user_id, None)
                    self._locks.pop(user_id, None)

    async def _process(self, user_id: int) -> None:
        async with self._user_lock(user_id):
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

        await QueryExperienceESRepo(
            self._es.get_client()
        ).delete_many(experience_ids)

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

    async def _list_due_user_ids(self) -> list[int]:
        async with self._auth_postgres.session() as session:
            tasks = await AuthPGRepo(session).list_due_user_deletions(
                datetime.now(UTC),
                limit=self._config.cleanup_batch_size,
            )
            return [task.user_id for task in tasks]

    async def _worker_loop(self) -> None:
        while True:
            try:
                for user_id in await self._list_due_user_ids():
                    try:
                        await self._process(user_id)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            f"用户注销重试执行失败: user_id={user_id}"
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("批量重试用户注销任务失败")

            self._wake_event.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._config.user_deletion_retry_seconds,
                )


user_deletion_service = UserDeletionService(
    auth_postgres_client_manager,
    meta_postgres_client_manager,
    es_client_manager,
    docker_sandbox_manager,
    conversation_lifecycle_service,
    cfg.lifecycle,
)
