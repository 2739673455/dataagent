"""Planner Agent 构造器。"""

from collections.abc import Sequence
from typing import Any, cast

from deepagents import FilesystemMiddleware, create_deep_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.assistant.agents.middleware.user_message_context import (
    UserMessageContextMiddleware,
)
from app.assistant.agents.tools import create_shell_tool, create_view_image_tools
from app.sandbox.backend import DockerSandboxBackend

from .prompt import PLANNER_SYSTEM_PROMPT


def create_planner_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """使用直接工具调用编译 Planner Agent。"""
    filesystem = FilesystemMiddleware(
        backend=backend,
        tools=["read_file"],
    )
    return create_deep_agent(
        model=model,
        tools=[
            *tools,
            *create_view_image_tools(model),
            create_shell_tool(backend.shell_jobs),
        ],
        system_prompt=PLANNER_SYSTEM_PROMPT,
        middleware=cast(
            "Sequence[AgentMiddleware[Any, Any, Any]]",
            [
                filesystem,
                UserMessageContextMiddleware(
                    backend,
                    backend.conversation_dir,
                ),
                MessageTimestampMiddleware(),
            ],
        ),
        subagents=[],
        backend=backend,
        checkpointer=checkpointer,
        name="planner",
    )
