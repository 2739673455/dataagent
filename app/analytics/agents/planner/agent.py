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

from .prompt import build_planner_system_prompt

type InterpreterMode = Literal["thread", "turn", "call"]


def create_planner_agent(
    *,
    model: BaseChatModel,
    delegation_tool: BaseTool,
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    interpreter_mode: InterpreterMode | None,
    interpreter_ptc: Sequence[str | BaseTool],
    interpreter_timeout_seconds: float,
    interpreter_memory_limit_bytes: int,
    max_delegations_per_run: int,
    max_repair_rounds: int,
    max_repair_depth: int,
) -> CompiledStateGraph:
    """使用显式解释器与编排限制编译 Planner Agent"""
    interpreter = CodeInterpreterMiddleware(
        mode=interpreter_mode,
        ptc=list(interpreter_ptc),
        timeout=interpreter_timeout_seconds,
        memory_limit=interpreter_memory_limit_bytes,
        max_ptc_calls=max_delegations_per_run,
    )
    filesystem = FilesystemMiddleware(
        backend=backend,
        tools=["ls", "read_file", "glob", "grep"],
    )
    return create_deep_agent(
        model=model,
        tools=[delegation_tool],
        system_prompt=build_planner_system_prompt(
            max_delegations=max_delegations_per_run,
            max_repair_rounds=max_repair_rounds,
            max_repair_depth=max_repair_depth,
        ),
        middleware=cast(
            "Sequence[AgentMiddleware[Any, Any, Any]]",
            [filesystem, interpreter, MessageTimestampMiddleware()],
        ),
        subagents=[],
        backend=backend,
        checkpointer=checkpointer,
        name="planner",
    )
