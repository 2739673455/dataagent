"""专业 Agent 的能力定义与实例创建。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.analyst.prompt import ANALYST_SYSTEM_PROMPT
from app.assistant.agents.deferred import DynamicToolsMiddleware
from app.assistant.agents.explorer.prompt import EXPLORER_SYSTEM_PROMPT
from app.assistant.agents.filesystem import agent_skills_mount_path
from app.assistant.agents.reviewer.prompt import REVIEWER_SYSTEM_PROMPT
from app.assistant.agents.specialist_agent import create_specialist_agent
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.paths import SandboxSessionScope
from app.shared.contracts.analysis import (
    AGENT_TYPES,
    AgentSessionKey,
    AgentType,
)

_REQUIRED_EXPLORER_TOOLS = frozenset({"recall_context", "execute_sql"})
_RESERVED_MCP_TOOL_NAMES = frozenset(
    {
        "delegation",
        "task",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "shell",
        "view_image",
    }
)


@dataclass(frozen=True, slots=True)
class SpecialistDefinition:
    """一种专业 Agent 的提示词、技能目录和专属能力。"""

    system_prompt: str
    skill_directory: Path
    tools: tuple[BaseTool, ...] = ()
    skills: tuple[str, ...] = ()


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
            system_prompt=EXPLORER_SYSTEM_PROMPT,
            skill_directory=Path(__file__).parent / "explorer" / "skills",
            tools=tuple(tools_by_name[name] for name in sorted(explorer_tool_names)),
        ),
        "analyst": SpecialistDefinition(
            system_prompt=ANALYST_SYSTEM_PROMPT,
            skill_directory=Path(__file__).parent / "analyst" / "skills",
            skills=(agent_skills_mount_path("analyst"),),
        ),
        "reviewer": SpecialistDefinition(
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            skill_directory=Path(__file__).parent / "reviewer" / "skills",
        ),
    }


class SpecialistAgentFactory:
    """按 Session 创建绑定专属 Sandbox 的专业 Agent。"""

    def __init__(
        self,
        definitions: Mapping[AgentType, SpecialistDefinition],
        models: Mapping[AgentType, BaseChatModel],
        sandbox: DockerSandboxManager,
        checkpointer: BaseCheckpointSaver,
        initialize: Callable[[], Awaitable[None]],
        mcp_tools: Callable[[], list[BaseTool]],
    ) -> None:
        """绑定专业能力、模型和运行时依赖。"""
        expected_types = set(AGENT_TYPES)
        if set(definitions) != expected_types:
            raise ValueError("专业 Agent 定义必须覆盖所有 Agent 类型")
        if set(models) != expected_types:
            raise ValueError("专业 Agent 模型必须覆盖所有 Agent 类型")
        self._initialize = initialize
        self._mcp_tools = mcp_tools
        self._definitions = dict(definitions)
        self._models = dict(models)
        self._sandbox = sandbox
        self._checkpointer = checkpointer

    async def create(self, session_key: AgentSessionKey) -> CompiledStateGraph:
        """为一次委派创建专业 Agent 运行图。"""
        await self._initialize()
        backend = await self._sandbox.get_session_backend(
            session_key.user_id,
            session_key.conversation_id,
            session_key.analysis_id,
            session_key.agent_type,
            session_key.session_id,
        )
        return self.build(session_key, backend)

    def build(
        self, session_key: AgentSessionKey, backend: DockerSandboxBackend | None = None
    ) -> CompiledStateGraph:
        """编译 Session 图；模型、工具和文件后端在执行时解析。"""
        definition = self._definitions[session_key.agent_type]

        if backend is None:
            backend = self._sandbox.graph_backend(
                session_key.user_id,
                session_key.conversation_id,
                SandboxSessionScope(
                    session_key.analysis_id,
                    session_key.agent_type,
                    session_key.session_id,
                ),
            )
        return create_specialist_agent(
            name=session_key.agent_type,
            system_prompt=definition.system_prompt,
            skill_directory=definition.skill_directory,
            model=self._models[session_key.agent_type],
            tools=definition.tools,
            backend=backend,
            extra_middleware=[DynamicToolsMiddleware(self._mcp_tools)]
            if session_key.agent_type == "explorer"
            else [],
            checkpointer=self._checkpointer,
            skills=definition.skills,
        )
