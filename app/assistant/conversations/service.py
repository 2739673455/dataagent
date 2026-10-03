"""用户会话目录、历史读取与删除任务的完整用例。"""

from uuid import UUID

from loguru import logger

from app.assistant import contracts as chat_schema
from app.assistant import errors as chat_error
from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.conversations.scheduler import (
    enqueue_conversation_deletion,
    enqueue_conversation_title,
)
from app.assistant.execution.runs import ConversationRunService
from app.assistant.messages import history as conversation_history
from app.assistant.messages.title import initial_conversation_title
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.sessions.state_reader import AgentStateReader
from app.sandbox import DockerSandboxManager
from app.shared.contracts.analysis import AgentType


class ConversationService:
    """编排会话目录事务、历史消息读取和删除任务提交。"""

    def __init__(
        self,
        repository: ConversationPGRepo,
        runs: ConversationRunService,
        state_reader: AgentStateReader,
        sandbox: DockerSandboxManager,
        lifecycle: ConversationLifecycleService,
    ) -> None:
        """绑定会话仓储、执行服务、状态读取器和资源生命周期服务。"""
        self._repository = repository
        self._runs = runs
        self._state_reader = state_reader
        self._sandbox = sandbox
        self._lifecycle = lifecycle

    async def create(
        self, user_id: int, body: chat_schema.CreateConversationRequest
    ) -> chat_schema.ConversationResponse:
        """创建新对话。"""
        async with self._repository.session.begin():
            conversation = await self._repository.create(
                user_id,
                initial_conversation_title(body.initial_message),
                is_draft=body.is_draft,
            )
        initial_message = (body.initial_message or "").strip()
        if initial_message and not body.is_draft:
            try:
                enqueue_conversation_title(
                    user_id,
                    conversation.id,
                    conversation.title,
                    initial_message,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    f"提交会话标题任务失败，保留即时标题: conversation_id={conversation.id}"
                )

        logger.info(
            f"创建对话: conversation_id={conversation.id}, is_draft={conversation.is_draft}"
        )
        return chat_schema.ConversationResponse(
            conversation_id=conversation.id,
            title=conversation.title,
            update_at=conversation.update_at,
            running=False,
        )

    async def delete(
        self, user_id: int, body: chat_schema.DeleteConversationRequest
    ) -> None:
        """删除对话。"""

        for conversation_id in body.conversation_ids:
            if not await self._lifecycle.request_conversation_deletion(
                user_id,
                conversation_id,
            ):
                raise chat_error.ConversationNotFoundError
            try:
                enqueue_conversation_deletion(user_id, conversation_id)
            except Exception:  # noqa: BLE001
                logger.exception(
                    f"提交会话删除任务失败，等待定时补偿: conversation_id={conversation_id}"
                )

        logger.info(f"删除对话: conversation_ids={body.conversation_ids}")

    async def delete_draft(self, user_id: int, conversation_id: UUID) -> None:
        """幂等删除当前用户主动放弃的草稿会话。"""
        requested = await self._lifecycle.request_conversation_deletion(
            user_id,
            conversation_id,
            draft_only=True,
        )
        if requested:
            try:
                enqueue_conversation_deletion(user_id, conversation_id)
            except Exception:  # noqa: BLE001
                logger.exception(
                    f"提交草稿删除任务失败，等待定时补偿: conversation_id={conversation_id}"
                )

    async def rename(
        self, user_id: int, body: chat_schema.UpdateConversationRequest
    ) -> None:
        """修改对话信息。"""

        async with self._repository.session.begin():
            conversation = await self._repository.get(user_id, body.conversation_id)
            if conversation is None:
                raise chat_error.ConversationNotFoundError

            await self._repository.update(
                conversation,
                title=body.title,
            )
        logger.info(f"更新对话: conversation_id={body.conversation_id}")

    async def list(self, user_id: int) -> chat_schema.ConversationListResponse:
        """获取所有对话。"""
        conversations = await self._repository.list_by_user(user_id)
        running_conversation_ids = await self._runs.running_conversation_ids(user_id)
        logger.info(
            f"获取对话列表: conversation_ids={[item.id for item in conversations]}"
        )
        return chat_schema.ConversationListResponse(
            conversations=[
                chat_schema.ConversationResponse(
                    conversation_id=item.id,
                    title=item.title,
                    update_at=item.update_at,
                    running=item.id in running_conversation_ids,
                )
                for item in conversations
            ]
        )

    async def messages(
        self, user_id: int, conversation_id: UUID
    ) -> chat_schema.MessageListResponse:
        """从 LangGraph 状态获取某个对话的所有消息。"""
        conversation = await self._repository.get(user_id, conversation_id)
        if conversation is None:
            raise chat_error.ConversationNotFoundError
        messages = await conversation_history.list_messages(
            self._state_reader,
            self._sandbox,
            user_id,
            conversation_id,
        )
        logger.info(
            f"获取消息列表: conversation_id={conversation_id}, count={len(messages)}"
        )
        return chat_schema.MessageListResponse(messages=messages)

    async def delegation_messages(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        agent_type: AgentType,
        session_id: str,
        delegation_id: str,
    ) -> chat_schema.SubagentMessageListResponse:
        """读取一次 Specialist delegation 的公开工作消息。"""
        conversation = await self._repository.get(user_id, conversation_id)
        if conversation is None:
            raise chat_error.ConversationNotFoundError
        return await conversation_history.get_subagent_activity(
            self._state_reader,
            user_id,
            conversation_id,
            analysis_id,
            agent_type,
            session_id,
            delegation_id,
            files=self._sandbox,
        )
