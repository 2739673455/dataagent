"""数据查询 Agent 构造器"""

from collections.abc import Sequence

from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agents.data_query.prompt import DATA_QUERY_SYSTEM_PROMPT
from app.agents.data_query.semantic_recall_middleware import (
    SemanticRecallExpansionMiddleware,
)
from app.agents.shared.specialist import create_specialist_agent


def create_data_query_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译数据查询 Agent"""
    return create_specialist_agent(
        agent_type="data_query",
        model=model,
        tools=tools,
        system_prompt=DATA_QUERY_SYSTEM_PROMPT,
        backend=backend,
        checkpointer=checkpointer,
        store=store,
        skills=skills,
        middleware=[SemanticRecallExpansionMiddleware()],
    )
