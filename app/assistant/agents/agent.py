"""Planner 与专业 Agent 的公共图构造。"""

from collections.abc import Sequence
from typing import Literal

from deepagents import (
    FilesystemMiddleware,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.middleware.message_context import (
    MessageContextMiddleware,
)
from app.assistant.agents.tools import create_shell_tool, create_view_image_tools
from app.sandbox.backend import DockerSandboxBackend


def create_agent(
    *,
    name: str,
    system_prompt: str,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    sandbox: DockerSandboxBackend,
    filesystem: FilesystemMiddleware,
    checkpointer: BaseCheckpointSaver | Literal[False],
    skills: Sequence[str] | None = None,
    subagents: list[CompiledSubAgent] | None = None,
    middleware: Sequence[AgentMiddleware] = (),
) -> CompiledStateGraph:
    """按调用方配置装配工具、中间件和持久化状态，不区分 Agent 角色。"""
    model_params = dict(model._get_ls_params())  # pyright: ignore[reportPrivateUsage]
    register_harness_profile(
        f"{model_params['ls_provider']}:{model_params['ls_model_name']}",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    return create_deep_agent(
        model=model,
        tools=[
            *tools,
            *create_view_image_tools(model),
            create_shell_tool(sandbox.shell_jobs),
        ],
        system_prompt=system_prompt,
        middleware=[
            filesystem,
            *middleware,
            MessageContextMiddleware(
                filesystem.backend,
            ),
        ],
        backend=filesystem.backend,
        skills=list(skills) if skills is not None else None,
        subagents=subagents or [],
        checkpointer=checkpointer,
        name=name,
    )
