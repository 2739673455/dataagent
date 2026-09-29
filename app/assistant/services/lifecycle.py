"""会话资源生命周期编排。"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from app.assistant.errors import ConversationBusyError
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.run import ConversationRunService
from app.assistant.services.types import (
    conversation_lifecycle_lock_name,
)
from app.shared.errors.infrastructure import AdvisoryLockBusyError

if TYPE_CHECKING:
    from app.assistant.services.manager import AgentManager
    from app.sandbox.manager import DockerSandboxManager
    from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager


_DRAFT_TTL_MINUTES = 1440
_CLEANUP_BATCH_SIZE = 100


class ConversationLifecycleService:
    """统一删除会话状态和沙箱文件。"""

    def __init__(
        self,
        repository_factory: Callable[
            [], AbstractAsyncContextManager[ConversationPGRepo]
        ],
        lock_provider: LangGraphPostgresManager,
        agents: AgentManager,
        sandbox: DockerSandboxManager,
        runs: ConversationRunService | None = None,
    ) -> None:
        """初始化跨存储会话资源和生命周期锁依赖。"""
        self._repository_factory = repository_factory
        self._lock_provider = lock_provider
        self._agents = agents
        self._sandbox = sandbox
        self._runs = runs

    @asynccontextmanager
    async def lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[None]:
        """获取跨进程会话生命周期锁。"""
        async with self._lock_provider.advisory_lock(
            conversation_lifecycle_lock_name(user_id, conversation_id),
        ):
            yield

    async def request_conversation_deletion(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> bool:
        """写入删除墓碑并使会话立即从接口中消失。"""
        # 先确认会话存在且属于当前用户，再停止对应的执行。
        async with self._repository_factory() as repository:
            conversation = await repository.get(
                user_id, conversation_id, include_deleting=True
            )
            if conversation is None:
                return False
        if self._runs is not None:
            await self._runs.stop(user_id, conversation_id)
        try:
            async with (
                self.lock(user_id, conversation_id),
                self._repository_factory() as repository,
            ):
                conversation = await repository.get(
                    user_id,
                    conversation_id,
                    include_deleting=True,
                )
                if conversation is None:
                    return False
                if conversation.deletion_requested_at is None:
                    await repository.update(
                        conversation,
                        deletion_requested_at=datetime.now(UTC),
                    )
                return True
        except AdvisoryLockBusyError as exc:
            raise ConversationBusyError(
                detail="对话正在运行或清理，请稍后重试"
            ) from exc

    async def delete_conversation_resources(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        draft_expired_before: datetime | None = None,
    ) -> bool:
        """幂等删除一个会话的全部跨存储资源。"""
        async with self.lock(user_id, conversation_id):
            async with self._repository_factory() as repository:
                conversation = await repository.get(
                    user_id,
                    conversation_id,
                    include_deleting=True,
                )
            if conversation is None:
                return False
            if draft_expired_before is not None and (
                not conversation.is_draft
                or conversation.update_at > draft_expired_before
            ):
                return False

            await self._agents.delete_agent_under_lifecycle_lock(
                user_id,
                conversation_id,
            )
            await self._sandbox.delete_conversation(user_id, conversation_id)
            async with self._repository_factory() as repository:
                await repository.delete(user_id, conversation_id)
            return True

    async def cleanup_expired_drafts(self) -> int:
        """执行一批过期草稿回收。"""
        cutoff = datetime.now(UTC) - timedelta(minutes=_DRAFT_TTL_MINUTES)
        async with self._repository_factory() as repository:
            drafts = await repository.list_expired_drafts(
                cutoff,
                limit=_CLEANUP_BATCH_SIZE,
            )
        deleted = 0
        for draft in drafts:
            if await self.delete_conversation_resources(
                draft.user_id,
                draft.id,
                draft_expired_before=cutoff,
            ):
                deleted += 1
        return deleted

    async def cleanup_pending_deletions(self) -> int:
        """执行一批已有删除墓碑的物理资源清理。"""
        async with self._repository_factory() as repository:
            conversations = await repository.list_pending_deletions(
                limit=_CLEANUP_BATCH_SIZE
            )
        deleted = 0
        for conversation in conversations:
            if await self.delete_conversation_resources(
                conversation.user_id,
                conversation.id,
            ):
                deleted += 1
        return deleted
