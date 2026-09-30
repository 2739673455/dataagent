"""Planner Agent 构造器。"""

from collections.abc import Sequence
from typing import Any, cast

from deepagents import create_deep_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.filesystem import build_agent_filesystem
from app.assistant.agents.middleware.eval_delegations import (
    EvalDelegationMiddleware,
)
from app.assistant.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.assistant.agents.middleware.user_message_context import (
    UserMessageContextMiddleware,
)
from app.assistant.agents.tools import (
    create_shell_tools,
    create_view_image_tool,
    supports_view_image_tool,
)
from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.shell_jobs import ShellJobRuntime
from app.assistant.resource_loader import load_prompt
from app.sandbox import DockerSandboxBackend

_INTERPRETER_PTC = ("delegation",)


def create_planner_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    session_service: AgentSessionService,
    shell_jobs: ShellJobRuntime,
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
    resolved_backend, filesystem = build_agent_filesystem(
        backend,
        tools=["read_file"],
    )
    return create_deep_agent(
        model=model,
        tools=[
            *tools,
            *([create_view_image_tool()] if supports_view_image_tool(model) else []),
            *create_shell_tools(shell_jobs),
        ],
        system_prompt=load_prompt("agents/planner"),
        middleware=cast(
            "Sequence[AgentMiddleware[Any, Any, Any]]",
            [
                EvalDelegationMiddleware(session_service),
                filesystem,
                interpreter,
                UserMessageContextMiddleware(
                    resolved_backend,
                    backend.conversation_dir,
                    shell_jobs,
                ),
                MessageTimestampMiddleware(),
            ],
        ),
        subagents=[],
        backend=resolved_backend,
        checkpointer=checkpointer,
        name="planner",
    )
