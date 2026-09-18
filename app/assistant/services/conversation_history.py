"""聊天与专业 Agent 历史消息读取。"""

from uuid import UUID

from langchain_core.messages import BaseMessage

from app.assistant.agents.middleware.semantic_recall_expansion import (
    expand_semantic_recall_messages_for_display,
)
from app.assistant.contracts import chat as chat_schema
from app.assistant.services.contracts import (
    AgentRuntimeManager,
    ConversationFileInspector,
)
from app.assistant.services.message_projection import (
    langchain_message_to_schema,
    langchain_message_to_schema_with_artifacts,
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
) -> chat_schema.SubagentMessageListResponse | None:
    """读取一次 Specialist delegation 的公开工作消息和状态。"""
    activity = await agents.read_delegation_activity(
        user_id,
        conversation_id,
        analysis_id,
        agent_type,
        session_id,
        delegation_id,
    )
    if activity is None:
        return None
    messages = await expand_semantic_recall_messages_for_display(
        activity.messages,
        user_id,
        conversation_id,
    )
    return chat_schema.SubagentMessageListResponse(
        status=activity.status,
        messages=[
            schema
            for message in messages
            if (schema := langchain_message_to_schema(message, conversation_id))
            is not None
        ],
    )
