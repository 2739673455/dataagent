"""Planner Agent 构造器"""

from collections.abc import Sequence
from typing import Any, Literal, cast

from deepagents import FilesystemMiddleware, create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.analytics.agents.message_timestamp_middleware import MessageTimestampMiddleware
from app.analytics.agents.user_message_metadata import (
    UserMessageMetadataMiddleware,
)

from .prompt import build_planner_system_prompt

type InterpreterMode = Literal["thread", "turn", "call"]


def create_planner_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    interpreter_mode: InterpreterMode | None,
    interpreter_ptc: Sequence[str | BaseTool],
    interpreter_memory_limit_bytes: int,
) -> CompiledStateGraph:
    """使用显式解释器配置编译 Planner Agent"""
    interpreter = CodeInterpreterMiddleware(
        mode=interpreter_mode,
        ptc=list(interpreter_ptc),
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
        tools=tools,
        system_prompt=build_planner_system_prompt(),
        middleware=cast(
            "Sequence[AgentMiddleware[Any, Any, Any]]",
            [
                filesystem,
                interpreter,
                UserMessageMetadataMiddleware(),
                MessageTimestampMiddleware(),
            ],
        ),
        subagents=[],
        backend=backend,
        checkpointer=checkpointer,
        name="planner",
    )
