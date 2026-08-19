"""分析 Agent 构造器"""

from collections.abc import Sequence

from deepagents import FilesystemMiddleware, create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agents.analyst.prompt import ANALYST_SYSTEM_PROMPT
from app.agents.contracts import SpecialistResult
from app.conf.app_config import cfg


def create_analyst_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译分析 Agent"""
    filesystem = FilesystemMiddleware(
        backend=backend,
        tools="all",
        max_execute_timeout=cfg.sandbox.execute_timeout_seconds,
    )
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=ANALYST_SYSTEM_PROMPT,
        middleware=[filesystem],
        backend=backend,
        skills=list(skills),
        subagents=[],
        response_format=SpecialistResult,
        checkpointer=checkpointer,
        store=store,
        name="analyst",
    )
