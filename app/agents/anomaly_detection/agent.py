"""异常检测 Agent 构造器"""

from collections.abc import Sequence

from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agents.anomaly_detection.prompt import ANOMALY_DETECTION_SYSTEM_PROMPT
from app.agents.shared.specialist import create_specialist_agent


def create_anomaly_detection_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译异常检测 Agent"""
    return create_specialist_agent(
        agent_type="anomaly_detection",
        model=model,
        tools=tools,
        system_prompt=ANOMALY_DETECTION_SYSTEM_PROMPT,
        backend=backend,
        checkpointer=checkpointer,
        store=store,
        skills=skills,
    )
