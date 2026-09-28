"""聊天与专业 Agent 历史消息读取。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.assistant.errors import SubagentRunNotFoundError
from app.assistant.events import schemas as chat_schema
from app.assistant.events.projection import (
    project_messages,
)

if TYPE_CHECKING:
    from app.assistant.execution.manager import AgentManager
    from app.sandbox.manager import DockerSandboxManager


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


async def get_subagent_activity(
    agents: AgentManager,
    files: DockerSandboxManager,
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
        messages=await project_messages(
            activity.messages, files, user_id, conversation_id
        ),
    )
