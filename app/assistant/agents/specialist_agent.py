"""专业 Agent 的共用构造逻辑。"""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from deepagents import create_deep_agent
from deepagents.graph import DeepAgentState
from langchain.agents.middleware.types import AgentMiddleware
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.contracts import SpecialistResult
from app.assistant.agents.filesystem import build_specialist_filesystem
from app.assistant.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.assistant.agents.middleware.user_message_context import (
    UserMessageContextMiddleware,
)
from app.assistant.agents.shell_jobs import ShellJobRuntime
from app.assistant.agents.tools import create_shell_tools, create_view_image_tools
from app.sandbox.backend import DockerSandboxBackend


def _merge_delegation_records(
    current: dict[str, object],
    updates: dict[str, object],
) -> dict[str, object]:
    """按 delegation ID 覆盖单条状态，同时保留同 Session 的历史记录。"""
    return {**current, **updates}


def _specialist_response_format(
    model: BaseChatModel,
) -> ProviderStrategy[SpecialistResult] | ToolStrategy[SpecialistResult]:
    """按模型能力选择 Specialist 结构化输出策略。"""
    if model.profile and model.profile.get("structured_output"):
        # 原生 JSON Schema 能让 Provider 在生成时约束最终跨 Agent 结果。
        return ProviderStrategy(SpecialistResult, strict=True)
    return ToolStrategy(SpecialistResult)


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
    skill_directory: Path,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    shell_jobs: ShellJobRuntime,
    skills: Sequence[str],
    extra_middleware: Sequence[AgentMiddleware] = (),
) -> CompiledStateGraph:
    """编译共享文件、附件和 Shell 生命周期的专业 Agent。"""
    resolved_backend, filesystem = build_specialist_filesystem(
        backend,
        skill_directory,
        skills,
    )
    return create_deep_agent(
        model=model,
        tools=[
            *tools,
            *create_view_image_tools(model),
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
        response_format=_specialist_response_format(model),
        state_schema=SpecialistAgentState,
        checkpointer=checkpointer,
        name=name,
    )
