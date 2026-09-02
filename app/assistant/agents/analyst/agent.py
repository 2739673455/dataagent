"""分析 Agent 构造器。"""

from collections.abc import Sequence
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.analyst.prompt import ANALYST_SYSTEM_PROMPT
from app.assistant.agents.shell_jobs import ShellJobRuntime
from app.assistant.agents.specialist_agent import create_specialist_agent
from app.sandbox.backend import DockerSandboxBackend


def create_analyst_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    shell_jobs: ShellJobRuntime,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译分析 Agent。"""
    return create_specialist_agent(
        name="analyst",
        system_prompt=ANALYST_SYSTEM_PROMPT,
        skill_directory=Path(__file__).with_name("skills"),
        model=model,
        tools=tools,
        backend=backend,
        checkpointer=checkpointer,
        shell_jobs=shell_jobs,
        skills=skills,
    )
