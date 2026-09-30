"""专业 Agent 的共用构造逻辑。"""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from deepagents import create_deep_agent
from deepagents.graph import DeepAgentState
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.filesystem import build_agent_filesystem
from app.assistant.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.assistant.agents.middleware.user_message_context import (
    UserMessageContextMiddleware,
)
from app.assistant.agents.tools import (
    create_shell_tools,
    create_view_image_tool,
    supports_view_image_tool,
)
from app.assistant.execution.shell_jobs import ShellJobRuntime
from app.sandbox import DockerSandboxBackend


def _merge_delegation_records(
    current: dict[str, object],
    updates: dict[str, object],
) -> dict[str, object]:
    """按 delegation ID 覆盖单条状态，同时保留同 Session 的历史记录。"""
    return {**current, **updates}


class SpecialistAgentState(DeepAgentState):
    """增加显式 delegation 状态的专业 Agent Checkpoint。"""

    delegation_records: Annotated[
        dict[str, object],
        _merge_delegation_records,
    ]


def create_specialist_agent(
    *,
    name: str,
    system_prompt: str,
    skill_directory: Path | None,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    shell_jobs: ShellJobRuntime,
    skills: Sequence[str],
    extra_middleware: Sequence[AgentMiddleware] = (),
) -> CompiledStateGraph:
    """编译共享文件、附件和 Shell 生命周期的专业 Agent。"""
    resolved_backend, filesystem = build_agent_filesystem(
        backend,
        tools=["read_file", "write_file", "edit_file"],
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
            UserMessageContextMiddleware(
                resolved_backend,
                backend.conversation_dir,
                shell_jobs,
            ),
            *extra_middleware,
            MessageTimestampMiddleware(),
        ],
        backend=resolved_backend,
        skills=list(skills),
        subagents=[],
        state_schema=SpecialistAgentState,
        checkpointer=checkpointer,
        name=name,
    )
