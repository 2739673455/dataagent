"""用户回合入口：会话校验与目录更新 → 标题调度 → Run 启动或恢复。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from uuid import UUID

from app.assistant.errors import (
    ConversationNotFoundError,
    ConversationNotResumableError,
)
from app.assistant.events import schemas as chat_contract
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.run import ConversationRunService
from app.assistant.tasks import ConversationTasks

if TYPE_CHECKING:
    from app.assistant.services.manager import AgentManager


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
        message: chat_contract.UserMessageRequest,
    ) -> AsyncGenerator[chat_contract.ChatStreamEventPayload]:
        """更新 Conversation 状态、提交标题任务并启动 Planner Run。"""

        async def prepare() -> None:
            """在 Run 持有生命周期锁时更新目录并调度标题。"""
            title_submission: tuple[UUID, str] | None = None
            async with self._repository.session.begin():
                conversation = await self._repository.get(user_id, conversation_id)
                if conversation is None:
                    raise ConversationNotFoundError

                user_text = "\n".join(
                    part.text
                    for part in message.parts
                    if isinstance(part, chat_contract.TextContent)
                ).strip()
                if user_text and (
                    conversation.is_draft or conversation.title == "新对话"
                ):
                    conversation = await self._repository.update(
                        conversation,
                        title=user_text[:64],
                        is_draft=False,
                    )
                    title_submission = (
                        conversation.id,
                        user_text,
                    )
                elif conversation.is_draft:
                    await self._repository.update(conversation, is_draft=False)
                else:
                    await self._repository.update(conversation)

            if title_submission is not None:
                target_id, source = title_submission
                self._tasks.generate_title(
                    user_id,
                    target_id,
                    source,
                )

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
    ) -> AsyncGenerator[chat_contract.ChatStreamEventPayload]:
        """验证 Conversation 和 Checkpoint 后恢复 Planner Run。"""

        async def prepare() -> None:
            """检查与执行使用同一把锁，并在进入模型前结束数据库事务。"""
            async with self._repository.session.begin():
                if await self._repository.get(user_id, conversation_id) is None:
                    raise ConversationNotFoundError
            if not await self._agents.can_resume_planner(
                user_id,
                conversation_id,
            ):
                raise ConversationNotResumableError

        return await self._runs.start(user_id, conversation_id, None, prepare=prepare)
