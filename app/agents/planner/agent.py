"""Planner Agent 构造器"""

from collections.abc import Sequence
from typing import Literal

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from .prompt import build_planner_system_prompt
from .quickjs_worker import install_responsive_quickjs_worker

type InterpreterMode = Literal["thread", "turn", "call"]

install_responsive_quickjs_worker()


def create_planner_agent(
    *,
    model: BaseChatModel,
    delegate_agent: BaseTool,
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
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
    return create_deep_agent(
        model=model,
        tools=[delegate_agent],
        system_prompt=build_planner_system_prompt(
            max_delegations=max_delegations_per_run,
            max_repair_rounds=max_repair_rounds,
            max_repair_depth=max_repair_depth,
        ),
        middleware=[interpreter],
        subagents=[],
        backend=backend,
        checkpointer=checkpointer,
        store=store,
        name="planner",
    )
