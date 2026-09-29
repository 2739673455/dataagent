"""会话资源生命周期编排。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.run import ConversationRunService

if TYPE_CHECKING:
    from app.assistant.services.manager import AgentManager
    from app.sandbox.manager import DockerSandboxManager


_DRAFT_TTL_MINUTES = 1440
_CLEANUP_BATCH_SIZE = 100


class ConversationLifecycleService:
    """统一删除会话状态和沙箱文件。"""

    def __init__(
        self,
        repository_factory: Callable[
            [], AbstractAsyncContextManager[ConversationPGRepo]
        ],
        agents: AgentManager,
        sandbox: DockerSandboxManager,
        runs: ConversationRunService | None = None,
    ) -> None:
        """初始化会话资源与进程内删除协调。"""
        self._repository_factory = repository_factory
        self._deletion_lock = asyncio.Lock()
        self._agents = agents
        self._sandbox = sandbox
        self._runs = runs

    async def request_conversation_deletion(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> bool:
        """先隐藏会话阻止新回合，再取消并等待当前执行退出。"""
        async with self._deletion_lock:
            async with self._repository_factory() as repository:
                conversation = await repository.get(
                    user_id, conversation_id, include_deleting=True
                )
                if conversation is None:
                    return False
                if conversation.deletion_requested_at is None:
                    await repository.update(
                        user_id,
                        conversation_id,
                        deletion_requested_at=datetime.now(UTC),
                    )
            if self._runs is not None:
                await self._runs.stop(user_id, conversation_id)
            return True

    async def delete_conversation_resources(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        draft_expired_before: datetime | None = None,
    ) -> bool:
        """串行执行幂等资源清理，避免即时删除与周期补偿重复清理。"""
        async with self._deletion_lock:
            if (
                draft_expired_before is not None
                and self._runs is not None
                and await self._runs.is_running(user_id, conversation_id)
            ):
                return False
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
                if conversation.deletion_requested_at is None:
                    await repository.update(
                        user_id,
                        conversation_id,
                        deletion_requested_at=datetime.now(UTC),
                    )
            if self._runs is not None:
                await self._runs.stop(user_id, conversation_id)
            await self._agents.delete_conversation_state(user_id, conversation_id)
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
