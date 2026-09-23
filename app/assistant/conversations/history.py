"""聊天与专业 Agent 历史消息读取。"""

from uuid import UUID

from langchain_core.messages import BaseMessage

from app.assistant.errors import SubagentRunNotFoundError
from app.assistant.events import schemas as chat_schema
from app.assistant.events.projection import (
    langchain_message_to_schema,
    langchain_message_to_schema_with_artifacts,
)
from app.assistant.execution.contracts import (
    AgentRuntimeManager,
    ConversationFileInspector,
)


async def list_messages(
    agents: AgentRuntimeManager,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
) -> list[chat_schema.MessageResponse]:
    """从 LangGraph 最新线程状态读取消息。"""
    state = await agents.read_planner_state(user_id, conversation_id)
    messages = state.values.get("messages", [])
    if not isinstance(messages, list):
        return []

    result: list[chat_schema.MessageResponse] = []
    for message in messages:
        if not isinstance(message, BaseMessage):
            continue
        if schema := await langchain_message_to_schema_with_artifacts(
            message,
            files,
            user_id,
            conversation_id,
        ):
            result.append(schema)
    return result


async def get_subagent_activity(
    agents: AgentRuntimeManager,
    user_id: int,
    conversation_id: UUID,
    analysis_id: str,
    agent_type: str,
    session_id: str,
    delegation_id: str,
) -> chat_schema.SubagentMessageListResponse:
    """读取一次 Specialist delegation 的公开工作消息和状态。"""
    try:
        activity = await agents.read_delegation_activity(
            user_id,
            conversation_id,
            analysis_id,
            agent_type,
            session_id,
            delegation_id,
        )
    except ValueError as exc:
        raise SubagentRunNotFoundError from exc
    if activity is None:
        raise SubagentRunNotFoundError
    return chat_schema.SubagentMessageListResponse(
        status=activity.status,
        messages=[
            schema
            for message in activity.messages
            if (schema := langchain_message_to_schema(message, conversation_id))
            is not None
        ],
    )
