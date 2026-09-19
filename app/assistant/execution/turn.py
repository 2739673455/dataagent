"""用户回合入口：会话校验与目录更新 → 标题调度 → Run 启动或恢复。"""

from collections.abc import AsyncGenerator
from uuid import UUID

from loguru import logger

from app.assistant.conversations.title import initial_conversation_title
from app.assistant.errors import (
    ConversationNotFoundError,
    ConversationNotResumableError,
)
from app.assistant.events import schemas as chat_contract
from app.assistant.execution.contracts import AgentRuntimeManager
from app.assistant.execution.run import ConversationRunService
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.task_scheduler import enqueue_conversation_title


class ConversationTurnService:
    """提交新回合或恢复已有 Planner 回合。"""

    def __init__(
        self,
        *,
        repository: ConversationPGRepo,
        runs: ConversationRunService,
        agents: AgentRuntimeManager,
    ) -> None:
        """绑定 Conversation 持久化、后台 Run 和 Checkpoint 读取能力。"""
        self._repository = repository
        self._runs = runs
        self._agents = agents

    async def start(
        self,
        user_id: int,
        conversation_id: UUID,
        message: chat_contract.UserMessageRequest,
    ) -> AsyncGenerator[chat_contract.ChatStreamEventPayload]:
        """更新 Conversation 状态、提交标题任务并启动 Planner Run。"""

        async def prepare() -> None:
            """在 Run 持有生命周期锁时更新目录并调度标题。"""
            title_submission: tuple[UUID, str, str] | None = None
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
                    conversation.is_draft
                    or conversation.title == initial_conversation_title(None)
                ):
                    conversation = await self._repository.update(
                        conversation,
                        title=initial_conversation_title(user_text),
                        is_draft=False,
                    )
                    title_submission = (
                        conversation.id,
                        conversation.title,
                        user_text,
                    )
                elif conversation.is_draft:
                    await self._repository.update(conversation, is_draft=False)
                else:
                    await self._repository.update(conversation)

            if title_submission is not None:
                target_id, expected_title, source = title_submission
                try:
                    enqueue_conversation_title(
                        user_id,
                        target_id,
                        expected_title,
                        source,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "提交会话标题任务失败，等待定时补偿: "
                        f"conversation_id={target_id}"
                    )

        return await self._runs.start_turn(
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

        return await self._runs.resume_turn(user_id, conversation_id, prepare=prepare)
