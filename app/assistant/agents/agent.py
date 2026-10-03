"""Planner 与专业 Agent 共用的构造逻辑。"""

from collections.abc import Sequence

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
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
from app.sandbox.contracts import SandboxReadonlyMount


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
    skill_mount: SandboxReadonlyMount | None = None,
    state_schema: type[DeepAgentState] | None = None,
) -> CompiledStateGraph:
    """按显式工具范围、技能和状态类型编译 Agent。"""
    # 专家委派由会话执行层管理，关闭框架自动添加的通用子 Agent。
    model_info = model._get_ls_params()  # pyright: ignore[reportPrivateUsage]
    provider = model_info.get("ls_provider")
    if not provider:
        raise ValueError("模型缺少供应商标识，无法配置 Agent 行为")
    model_name = model_info.get("ls_model_name")
    register_harness_profile(
        f"{provider}:{model_name}" if model_name else provider,
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    resolved_backend, filesystem = build_agent_filesystem(
        backend,
        tools=filesystem_tools,
        skill_mount=skill_mount,
    )
    return create_deep_agent(
        model=model,
        tools=[
            *tools,
            *(
                [create_view_image_tool(backend.workspace_dir)]
                if supports_view_image_tool(model)
                else []
            ),
            *create_shell_tools(shell_jobs),
        ],
        system_prompt=system_prompt,
        middleware=[
            filesystem,
            MessageContextMiddleware(
                resolved_backend,
                backend.conversation_dir,
                shell_jobs,
                working_directory=backend.workspace_dir,
            ),
        ],
        backend=resolved_backend,
        skills=[f"{skill_mount.target}/"] if skill_mount is not None else [],
        subagents=[],
        state_schema=state_schema,
        checkpointer=checkpointer,
        name=name,
    )
