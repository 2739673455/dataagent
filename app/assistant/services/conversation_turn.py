"""Conversation 用户回合的应用用例。"""

from collections.abc import AsyncGenerator
from uuid import UUID

from loguru import logger

from app.assistant.contracts import chat as chat_contract
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services import chat
from app.assistant.services.contracts import AgentRuntimeManager
from app.assistant.services.conversation_lifecycle import ConversationLifecycleService
from app.assistant.services.conversation_run import ConversationRunService
from app.assistant.services.conversation_title import initial_conversation_title
from app.assistant.tasks import enqueue_conversation_title


class ConversationMissingError(RuntimeError):
    """目标 Conversation 不存在或不属于当前用户。"""


class ConversationTurnService:
    """提交新回合或恢复已有 Planner 回合。"""

    def __init__(
        self,
        *,
        repository: ConversationPGRepo,
        lifecycle: ConversationLifecycleService,
        runs: ConversationRunService,
        agents: AgentRuntimeManager,
    ) -> None:
        """绑定 Conversation 持久化、生命周期锁和 Agent Run 能力。"""
        self._repository = repository
        self._lifecycle = lifecycle
        self._runs = runs
        self._agents = agents

    async def start(
        self,
        user_id: int,
        conversation_id: UUID,
        message: chat_contract.UserMessageRequest,
    ) -> AsyncGenerator[chat_contract.ChatStreamEventPayload]:
        """更新 Conversation 状态、提交标题任务并启动 Planner Run。"""
        title_submission: tuple[UUID, str, str] | None = None
        async with self._lifecycle.lock(user_id, conversation_id):
            async with self._repository.session.begin():
                conversation = await self._repository.get(user_id, conversation_id)
                if conversation is None:
                    raise ConversationMissingError

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
        return await self._runs.start_turn(user_id, conversation_id, message)

    async def resume(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[chat_contract.ChatStreamEventPayload]:
        """验证 Conversation 和 Checkpoint 后恢复 Planner Run。"""
        if await self._repository.get(user_id, conversation_id) is None:
            raise ConversationMissingError
        if not await chat.can_resume_agent_turn(
            self._agents,
            user_id,
            conversation_id,
        ):
            raise chat.PlannerTurnNotResumableError
        return await self._runs.resume_turn(user_id, conversation_id)
