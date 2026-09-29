"""聊天历史消息读取。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.assistant.events import schemas as chat_schema
from app.assistant.events.projection import (
    project_messages,
)

if TYPE_CHECKING:
    from app.assistant.services.manager import AgentManager
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
