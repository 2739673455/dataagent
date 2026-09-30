"""Planner 与专业 Agent 共用的构造逻辑。"""

from collections.abc import Sequence
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.graph import DeepAgentState
from deepagents.middleware.filesystem import FsToolName
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.filesystem import build_agent_filesystem
from app.assistant.agents.middleware.message_context import (
    MessageContextMiddleware,
)
from app.assistant.agents.tools.shell import create_shell_tools
from app.assistant.agents.tools.view_image import (
    create_view_image_tool,
    supports_view_image_tool,
)
from app.assistant.execution.shell_jobs import ShellJobRuntime
from app.sandbox import DockerSandboxBackend


def create_agent(
    *,
    name: str,
    system_prompt: str,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    shell_jobs: ShellJobRuntime,
    filesystem_tools: Sequence[FsToolName],
    skill_directory: Path | None = None,
    skills: Sequence[str] = (),
    state_schema: type[DeepAgentState] | None = None,
) -> CompiledStateGraph:
    """按显式工具范围、技能和状态类型编译 Agent。"""
    resolved_backend, filesystem = build_agent_filesystem(
        backend,
        tools=filesystem_tools,
        skill_directory=skill_directory,
        skills=skills,
    )
    return create_deep_agent(
        model=model,
        tools=[
            *tools,
            *([create_view_image_tool()] if supports_view_image_tool(model) else []),
            *create_shell_tools(shell_jobs),
        ],
        system_prompt=system_prompt,
        middleware=[
            filesystem,
            MessageContextMiddleware(
                resolved_backend,
                backend.conversation_dir,
                shell_jobs,
            ),
        ],
        backend=resolved_backend,
        skills=list(skills),
        subagents=[],
        state_schema=state_schema,
        checkpointer=checkpointer,
        name=name,
    )
