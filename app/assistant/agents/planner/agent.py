"""Planner Agent 构造器。"""

from collections.abc import Sequence
from typing import Any, cast

from deepagents import FilesystemMiddleware, create_deep_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.middleware.eval_delegations import (
    EvalDelegationMiddleware,
)
from app.assistant.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.assistant.agents.middleware.user_message_attachments import (
    UserMessageAttachmentMiddleware,
)
from app.assistant.agents.middleware.user_message_metadata import (
    UserMessageMetadataMiddleware,
)
from app.assistant.agents.session_service import AgentSessionService
from app.assistant.agents.tools import create_view_image_tools
from app.sandbox.backend import DockerSandboxBackend

from .prompt import PLANNER_SYSTEM_PROMPT

_INTERPRETER_PTC = ("delegation", "list_sessions", "delete_session")


def create_planner_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    session_service: AgentSessionService,
    interpreter_memory_limit_bytes: int,
) -> CompiledStateGraph:
    """使用显式解释器配置编译 Planner Agent。"""
    interpreter = CodeInterpreterMiddleware(
        mode="thread",
        ptc=list(_INTERPRETER_PTC),
        timeout=float("inf"),
        memory_limit=interpreter_memory_limit_bytes,
        max_ptc_calls=None,
    )
    filesystem = FilesystemMiddleware(
        backend=backend,
        tools=["ls", "read_file", "glob", "grep"],
    )
    return create_deep_agent(
        model=model,
        tools=[*tools, *create_view_image_tools(model)],
        system_prompt=PLANNER_SYSTEM_PROMPT,
        middleware=cast(
            "Sequence[AgentMiddleware[Any, Any, Any]]",
            [
                EvalDelegationMiddleware(session_service),
                filesystem,
                interpreter,
                UserMessageMetadataMiddleware(),
                UserMessageAttachmentMiddleware(backend, backend.conversation_dir),
                MessageTimestampMiddleware(),
            ],
        ),
        subagents=[],
        backend=backend,
        checkpointer=checkpointer,
        name="planner",
    )
