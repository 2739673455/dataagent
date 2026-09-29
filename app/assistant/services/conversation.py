"""会话回合、历史与附件读取，以及会话资源生命周期。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from app.assistant import errors
from app.assistant.errors import (
    ConversationNotFoundError,
    ConversationNotResumableError,
)
from app.assistant.messages import project_messages
from app.assistant.models import chat as chat_schema
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.run import ConversationRunService
from app.sandbox import SandboxFileTooLargeError, SandboxPathError

if TYPE_CHECKING:
    from app.assistant.agents.manager import AgentManager
    from app.assistant.services.tasks import ConversationTasks
    from app.sandbox import DockerSandboxManager

_DRAFT_TTL_MINUTES = 1440
_CLEANUP_BATCH_SIZE = 100


async def list_messages(
    agents: AgentManager,
    files: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
) -> list[chat_schema.MessageResponse]:
    """从 LangGraph 最新线程状态读取消息。"""
    state = await agents.read_planner_state(user_id, conversation_id)
    messages = state.values.get("messages", [])
    if not isinstance(messages, list):
        return []

    return await project_messages(messages, files, user_id, conversation_id)


class ConversationTurnService:
    """提交新回合或恢复已有 Planner 回合。"""

    def __init__(
        self,
        *,
        repository: ConversationPGRepo,
        runs: ConversationRunService,
        agents: AgentManager,
        tasks: ConversationTasks,
    ) -> None:
        """绑定 Conversation 持久化、后台 Run 和 Checkpoint 读取能力。"""
        self._repository = repository
        self._runs = runs
        self._agents = agents
        self._tasks = tasks

    async def start(
        self,
        user_id: int,
        conversation_id: UUID,
        message: chat_schema.UserMessageRequest,
    ) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
        """更新 Conversation 状态、提交标题任务并启动 Planner Run。"""

        async def prepare() -> None:
            """在注册的 Run 中更新目录并调度标题。"""
            async with self._repository.session.begin():
                conversation = await self._repository.get(user_id, conversation_id)
                if conversation is None:
                    raise ConversationNotFoundError

                user_text = "\n".join(part.text for part in message.parts).strip()
                generate_title = bool(user_text) and (
                    conversation.is_draft or conversation.title == "新对话"
                )
                await self._repository.update(
                    user_id,
                    conversation_id,
                    title=user_text[:64] if generate_title else None,
                    is_draft=False,
                )

            if generate_title:
                self._tasks.generate_title(user_id, conversation_id, user_text)

        return await self._runs.start(
            user_id,
            conversation_id,
            message,
            prepare=prepare,
        )

    async def resume(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
        """验证 Conversation 和 Checkpoint 后恢复 Planner Run。"""

        async def prepare() -> None:
            """检查会话与图状态，并在进入模型前结束数据库事务。"""
            async with self._repository.session.begin():
                if await self._repository.get(user_id, conversation_id) is None:
                    raise ConversationNotFoundError
            if not await self._agents.can_resume_planner(
                user_id,
                conversation_id,
            ):
                raise ConversationNotResumableError

        return await self._runs.start(user_id, conversation_id, None, prepare=prepare)


class AttachmentService:
    """校验会话归属并下载附件。"""

    def __init__(
        self,
        repository: ConversationPGRepo,
        sandbox: DockerSandboxManager,
    ) -> None:
        self._repository = repository
        self._sandbox = sandbox

    async def download(self, user_id: int, conversation_id: UUID, f_path: str) -> bytes:
        conversation = await self._repository.get(user_id, conversation_id)
        if conversation is None:
            raise errors.ConversationNotFoundError
        try:
            content = await self._sandbox.download_file(
                user_id,
                conversation_id,
                f_path,
            )
        except SandboxPathError:
            raise errors.PathTraversalError from None
        except FileNotFoundError:
            raise errors.AttachmentNotFoundError(detail=f_path) from None
        except SandboxFileTooLargeError:
            raise errors.AttachmentTooLargeError from None
        return content


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
                and self._runs.is_running(user_id, conversation_id)
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
        """执行一批已标记删除的物理资源清理。"""
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
