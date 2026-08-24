"""会话资源生命周期编排"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.analytics.agents.manager import (
    AgentManager,
    conversation_lifecycle_lock_name,
)
from app.analytics.repositories.conversation import ConversationPGRepo
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
)
from app.shared.config.app_config import LifecycleConfig


class ConversationLifecycleService:
    """统一删除会话状态、召回记录和沙盒文件"""

    def __init__(
        self,
        persistence_manager: LangGraphPostgresManager,
        agents: AgentManager,
        sandbox: DockerSandboxManager,
        config: LifecycleConfig,
        *,
        session_lock_timeout: float,
    ) -> None:
        self._persistence_manager = persistence_manager
        self._agents = agents
        self._sandbox = sandbox
        self._config = config
        self._session_lock_timeout = session_lock_timeout

    @asynccontextmanager
    async def lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[None]:
        """获取跨进程会话生命周期锁"""
        async with self._persistence_manager.advisory_lock(
            conversation_lifecycle_lock_name(user_id, conversation_id),
            timeout=self._session_lock_timeout,
        ):
            yield

    async def request_conversation_deletion(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        draft_only: bool = False,
    ) -> bool:
        """写入删除墓碑并使会话立即从接口中消失"""
        await self._agents.cancel_agent_execution(user_id, conversation_id)
        conversation_repo = ConversationPGRepo(self._persistence_manager.get_store())
        async with self.lock(user_id, conversation_id):
            conversation = await conversation_repo.get(
                user_id,
                conversation_id,
                include_deleting=True,
            )
            if conversation is None:
                return False
            if draft_only and not conversation.is_draft:
                return False
            if conversation.deletion_requested_at is None:
                await conversation_repo.update(
                    conversation,
                    deletion_requested_at=datetime.now(UTC),
                )
            return True

    async def delete_conversation_resources(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        draft_expired_before: datetime | None = None,
        draft_only: bool = False,
    ) -> bool:
        """幂等删除一个会话的全部跨存储资源"""
        conversation_repo = ConversationPGRepo(self._persistence_manager.get_store())
        recall_repo = SemanticRecallPGRepo(self._persistence_manager.get_store())
        async with self.lock(user_id, conversation_id):
            conversation = await conversation_repo.get(
                user_id,
                conversation_id,
                include_deleting=True,
            )
            if conversation is None:
                return False
            if draft_only and not conversation.is_draft:
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
            await recall_repo.delete_all(user_id, conversation_id)
            await self._sandbox.delete_conversation(user_id, conversation_id)
            await conversation_repo.delete(user_id, conversation_id)
            return True

    async def delete_user_conversations(self, user_id: int) -> None:
        """删除用户全部会话及残留召回记录"""
        conversation_repo = ConversationPGRepo(self._persistence_manager.get_store())
        while conversations := await conversation_repo.list_all_by_user(
            user_id,
            include_deleting=True,
        ):
            for conversation in conversations:
                await self.delete_conversation_resources(user_id, conversation.id)
        await self._agents.delete_user_agents(user_id)
        await SemanticRecallPGRepo(
            self._persistence_manager.get_store()
        ).delete_all_by_user(user_id)

    async def cleanup_expired_drafts(self) -> int:
        """执行一批过期草稿回收"""
        cutoff = datetime.now(UTC) - timedelta(minutes=self._config.draft_ttl_minutes)
        repository = ConversationPGRepo(self._persistence_manager.get_store())
        drafts = await repository.list_expired_drafts(
            cutoff,
            limit=self._config.cleanup_batch_size,
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
        """执行一批已有删除墓碑的物理资源清理"""
        repository = ConversationPGRepo(self._persistence_manager.get_store())
        conversations = await repository.list_pending_deletions(
            limit=self._config.cleanup_batch_size
        )
        deleted = 0
        for conversation in conversations:
            if await self.delete_conversation_resources(
                conversation.user_id,
                conversation.id,
            ):
                deleted += 1
        return deleted
