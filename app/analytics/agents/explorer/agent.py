"""数据探索 Agent 构造器"""

from collections.abc import Sequence

from deepagents import FilesystemMiddleware, create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.analytics.agents.contracts import SpecialistResult
from app.analytics.agents.explorer.prompt import EXPLORER_SYSTEM_PROMPT
from app.analytics.agents.explorer.semantic_recall_middleware import (
    SemanticRecallExpansionMiddleware,
)
from app.shared.config.app_config import cfg


def create_explorer_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译数据探索 Agent"""
    filesystem = FilesystemMiddleware(
        backend=backend,
        tools="all",
        max_execute_timeout=cfg.sandbox.execute_timeout_seconds,
    )
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=EXPLORER_SYSTEM_PROMPT,
        middleware=[filesystem, SemanticRecallExpansionMiddleware()],
        backend=backend,
        skills=list(skills),
        subagents=[],
        response_format=SpecialistResult,
        checkpointer=checkpointer,
        name="explorer",
    )
