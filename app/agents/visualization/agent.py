"""可视化 Agent 构造器"""

from collections.abc import Sequence

from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agents.shared.specialist import create_specialist_agent
from app.agents.visualization.prompt import VISUALIZATION_SYSTEM_PROMPT


def create_visualization_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译可视化 Agent"""
    return create_specialist_agent(
        agent_type="visualization",
        model=model,
        tools=tools,
        system_prompt=VISUALIZATION_SYSTEM_PROMPT,
        backend=backend,
        checkpointer=checkpointer,
        store=store,
        skills=skills,
    )
