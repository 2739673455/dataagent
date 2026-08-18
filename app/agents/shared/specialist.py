"""专业 Agent 公共构造逻辑"""

from collections.abc import Sequence

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agents.contracts import AgentType, SpecialistResult


def create_specialist_agent(
    *,
    agent_type: AgentType,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: str,
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    skills: Sequence[str],
) -> CompiledStateGraph:
    """使用共享持久化资源编译专业 Agent"""
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        skills=list(skills),
        subagents=[],
        response_format=SpecialistResult,
        checkpointer=checkpointer,
        store=store,
        name=agent_type,
    )
