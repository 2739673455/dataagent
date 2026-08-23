"""会话资源生命周期编排"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from loguru import logger

from app.analytics.agents.manager import (
    AgentManager,
    agent_manager,
    conversation_lifecycle_lock_name,
)
from app.analytics.repositories.conversation import ConversationPGRepo
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.sandbox.docker_manager import (
    DockerSandboxManager,
    docker_sandbox_manager,
)
from app.shared.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
    langgraph_postgres_manager,
)
from app.shared.config.app_config import LifecycleConfig, cfg


class ConversationLifecycleService:
    """统一删除会话状态、召回记录和沙盒文件"""

    def __init__(
        self,
        persistence_manager: LangGraphPostgresManager,
        agents: AgentManager,
        sandbox: DockerSandboxManager,
        config: LifecycleConfig,
    ) -> None:
        self._persistence_manager = persistence_manager
        self._agents = agents
        self._sandbox = sandbox
        self._config = config
        self._cleanup_task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()

    @asynccontextmanager
    async def lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[None]:
        """获取跨进程会话生命周期锁"""
        async with self._persistence_manager.advisory_lock(
            conversation_lifecycle_lock_name(user_id, conversation_id),
            timeout=cfg.agent.orchestration.session_lock_timeout,
        ):
            yield

    async def delete_conversation(
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
        if draft_expired_before is None and not draft_only:
            conversation = await conversation_repo.get(user_id, conversation_id)
            if conversation is None:
                return False
            await self._agents.cancel_agent_execution(
                user_id,
                conversation_id,
            )

        async with self.lock(user_id, conversation_id):
            conversation = await conversation_repo.get(user_id, conversation_id)
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
        while conversations := await conversation_repo.list_all_by_user(user_id):
            for conversation in conversations:
                await self.delete_conversation(user_id, conversation.id)
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
            try:
                if await self.delete_conversation(
                    draft.user_id,
                    draft.id,
                    draft_expired_before=cutoff,
                ):
                    deleted += 1
            except Exception:  # noqa: BLE001
                logger.exception(f"清理过期草稿会话失败: conversation_id={draft.id}")
        return deleted

    async def start(self) -> None:
        """启动草稿定期回收任务"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def close(self) -> None:
        """停止草稿定期回收任务"""
        task = self._cleanup_task
        self._cleanup_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _cleanup_loop(self) -> None:
        """持续回收已过期草稿"""
        while True:
            try:
                deleted = await self.cleanup_expired_drafts()
                if deleted:
                    logger.info(f"已清理 {deleted} 个过期草稿会话")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("批量清理过期草稿会话失败")

            self._wake_event.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._config.cleanup_interval_seconds,
                )


conversation_lifecycle_service = ConversationLifecycleService(
    langgraph_postgres_manager,
    agent_manager,
    docker_sandbox_manager,
    cfg.lifecycle,
)
