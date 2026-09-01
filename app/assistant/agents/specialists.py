"""专业 Agent 的能力定义与实例创建。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.analyst.agent import create_analyst_agent
from app.assistant.agents.explorer.agent import create_explorer_agent
from app.assistant.agents.reviewer.agent import create_reviewer_agent
from app.assistant.agents.shell_jobs import ShellJobRuntime
from app.assistant.agents.skills import agent_skills_mount_path
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.shared.contracts.analysis import (
    AGENT_TYPES,
    AgentSessionKey,
    AgentType,
    validate_agent_type,
)

_REQUIRED_EXPLORER_TOOLS = frozenset({"recall_context", "execute_sql"})
_RESERVED_MCP_TOOL_NAMES = frozenset(
    {
        "delegation",
        "task",
        "eval",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
        "list_shell_jobs",
        "get_shell_job",
        "cancel_shell_job",
        "view_image",
    }
)


class SpecialistBuilder(Protocol):
    """所有专业 Agent 构造器共享的调用协议。"""

    def __call__(
        self,
        *,
        model: BaseChatModel,
        tools: Sequence[BaseTool],
        backend: DockerSandboxBackend,
        checkpointer: BaseCheckpointSaver,
        shell_jobs: ShellJobRuntime,
        skills: Sequence[str] = (),
    ) -> CompiledStateGraph:
        """使用统一依赖构造一个可执行的专业 Agent 图。"""
        ...


@dataclass(frozen=True, slots=True)
class SpecialistAgentRun:
    """一次 delegation 共用的 Agent 图和 Shell Job Runtime。"""

    agent: CompiledStateGraph
    shell_jobs: ShellJobRuntime


@dataclass(frozen=True, slots=True)
class SpecialistDefinition:
    """一种专业 Agent 的构造器及其专属能力。"""

    builder: SpecialistBuilder
    tools: tuple[BaseTool, ...] = ()
    skills: tuple[str, ...] = ()

    @property
    def tool_names(self) -> frozenset[str]:
        """返回显式分配给该 Agent 的工具名。"""
        return frozenset(tool.name for tool in self.tools)


def build_specialist_definitions(
    explorer_tools: Iterable[BaseTool],
    explorer_mcp_tools: Iterable[BaseTool],
) -> dict[AgentType, SpecialistDefinition]:
    """构造专业 Agent 定义，并将数据访问能力限定给 Explorer。"""
    builtin_tools = tuple(explorer_tools)
    mcp_tools = tuple(explorer_mcp_tools)
    tools_by_name: dict[str, BaseTool] = {}
    for tool in (*builtin_tools, *mcp_tools):
        if tool.name in tools_by_name:
            raise ValueError(f"存在重名工具: {tool.name}")
        tools_by_name[tool.name] = tool

    mcp_tool_names = frozenset(tool.name for tool in mcp_tools)
    reserved_mcp_names = sorted(mcp_tool_names & _RESERVED_MCP_TOOL_NAMES)
    if reserved_mcp_names:
        raise ValueError(
            f"MCP 工具名称与运行时内置工具冲突: {', '.join(reserved_mcp_names)}"
        )

    missing_tools = sorted(_REQUIRED_EXPLORER_TOOLS - tools_by_name.keys())
    if missing_tools:
        raise ValueError(f"Explorer 缺少必需工具: {', '.join(missing_tools)}")

    explorer_tool_names = {
        *(tool.name for tool in builtin_tools),
        *mcp_tool_names,
    }
    return {
        "explorer": SpecialistDefinition(
            builder=create_explorer_agent,
            tools=tuple(tools_by_name[name] for name in sorted(explorer_tool_names)),
        ),
        "analyst": SpecialistDefinition(
            builder=create_analyst_agent,
            skills=(agent_skills_mount_path("analyst"),),
        ),
        "reviewer": SpecialistDefinition(builder=create_reviewer_agent),
    }


class SpecialistAgentFactory:
    """按 Session 创建绑定专属 Sandbox 的专业 Agent。"""

    def __init__(
        self,
        definitions: Mapping[AgentType, SpecialistDefinition],
        models: Mapping[AgentType, BaseChatModel],
        sandbox: DockerSandboxManager,
        checkpointer: BaseCheckpointSaver,
    ) -> None:
        """绑定专业能力、模型和运行时依赖。"""
        expected_types = set(AGENT_TYPES)
        if set(definitions) != expected_types:
            raise ValueError("专业 Agent 定义必须覆盖所有 Agent 类型")
        if set(models) != expected_types:
            raise ValueError("专业 Agent 模型必须覆盖所有 Agent 类型")
        self._definitions = dict(definitions)
        self._models = dict(models)
        self._sandbox = sandbox
        self._checkpointer = checkpointer

    async def create(self, session_key: AgentSessionKey) -> SpecialistAgentRun:
        """为一次委派创建专业 Agent 运行图。"""
        validate_agent_type(session_key.agent_type)
        definition = self._definitions[session_key.agent_type]
        backend = await self._sandbox.get_session_backend(
            session_key.user_id,
            session_key.conversation_id,
            session_key.analysis_id,
            session_key.agent_type,
            session_key.session_id,
        )
        shell_jobs = ShellJobRuntime(backend)
        agent = definition.builder(
            model=self._models[session_key.agent_type],
            tools=definition.tools,
            backend=backend,
            checkpointer=self._checkpointer,
            shell_jobs=shell_jobs,
            skills=definition.skills,
        )
        return SpecialistAgentRun(agent=agent, shell_jobs=shell_jobs)
