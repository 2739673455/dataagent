"""专业 Agent 定义与实例注册表"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.analytics.agents.analyst.prompt import ANALYST_SYSTEM_PROMPT
from app.analytics.agents.explorer.prompt import EXPLORER_SYSTEM_PROMPT
from app.analytics.agents.reviewer.prompt import REVIEWER_SYSTEM_PROMPT
from app.analytics.agents.visualizer.prompt import VISUALIZER_SYSTEM_PROMPT
from app.shared.contracts.analysis import (
    AGENT_TYPES,
    AgentSessionKey,
    AgentType,
    validate_agent_type,
)

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
    }
)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """一种专业 Agent 的集中配置"""

    description: str
    system_prompt: str
    platform_tools: frozenset[str] = frozenset()
    required_tools: frozenset[str] = frozenset()
    use_mcp: bool = False


_AGENT_SPECS: dict[AgentType, AgentSpec] = {
    "explorer": AgentSpec(
        description="检索语义目录并生成、检查、执行只读数据查询",
        system_prompt=EXPLORER_SYSTEM_PROMPT,
        platform_tools=frozenset(
            {
                "recall_context",
                "list_recalls",
                "get_recall",
                "merge_recalls",
                "delete_recalls",
                "execute_sql",
            }
        ),
        required_tools=frozenset(
            {
                "recall_context",
                "execute_sql",
            }
        ),
        use_mcp=True,
    ),
    "analyst": AgentSpec(
        description="对指标变化执行贡献分解、维度下钻和根因候选分析",
        system_prompt=ANALYST_SYSTEM_PROMPT,
    ),
    "reviewer": AgentSpec(
        description="审查上游数据、分析结论和产物，发现问题时发起修补",
        system_prompt=REVIEWER_SYSTEM_PROMPT,
    ),
    "visualizer": AgentSpec(
        description="生成可追溯的图表、表格和下载报告",
        system_prompt=VISUALIZER_SYSTEM_PROMPT,
    ),
}


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """一种专业 Agent 的静态能力定义"""

    agent_type: AgentType
    description: str
    system_prompt: str
    tools: tuple[BaseTool, ...]
    skills: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验专业 Agent 定义及其工具白名单"""
        validate_agent_type(self.agent_type)
        if not self.description.strip() or not self.system_prompt.strip():
            raise ValueError("智能体描述与 system_prompt 均不能为空")
        tool_names = [tool.name for tool in self.tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError(f"智能体类型 {self.agent_type} 包含重复工具")
        if "delegation" in tool_names or "task" in tool_names:
            raise ValueError("专家智能体不能拥有委派工具")

    @property
    def tool_names(self) -> frozenset[str]:
        """返回专业 Agent 的自定义 Tool 白名单"""
        return frozenset(tool.name for tool in self.tools)


def build_agent_definitions(
    platform_tools: Iterable[BaseTool],
    mcp_tools: Iterable[BaseTool],
) -> dict[AgentType, AgentDefinition]:
    """构造专业 Agent 定义并只向数据查询 Agent 分配 MCP 工具"""
    platform_tools = tuple(platform_tools)
    mcp_tools = tuple(mcp_tools)
    tools_by_name: dict[str, BaseTool] = {}
    for tool in (*platform_tools, *mcp_tools):
        if tool.name in tools_by_name:
            raise ValueError(f"存在重名工具: {tool.name}")
        tools_by_name[tool.name] = tool

    mcp_tool_names = frozenset(tool.name for tool in mcp_tools)
    reserved_mcp_names = sorted(mcp_tool_names & _RESERVED_MCP_TOOL_NAMES)
    if reserved_mcp_names:
        raise ValueError(
            f"MCP 工具名称与平台内置工具冲突: {', '.join(reserved_mcp_names)}"
        )

    missing_by_agent = {
        agent_type: sorted(spec.required_tools - tools_by_name.keys())
        for agent_type, spec in _AGENT_SPECS.items()
        if spec.required_tools - tools_by_name.keys()
    }
    if missing_by_agent:
        details = "; ".join(
            f"{agent_type}: {', '.join(names)}"
            for agent_type, names in missing_by_agent.items()
        )
        raise ValueError(f"缺少必需的专家工具: {details}")

    definitions: dict[AgentType, AgentDefinition] = {}
    for agent_type in AGENT_TYPES:
        spec = _AGENT_SPECS[agent_type]
        allowed_tool_names = spec.platform_tools
        if spec.use_mcp:
            allowed_tool_names |= mcp_tool_names
        definitions[agent_type] = AgentDefinition(
            agent_type=agent_type,
            description=spec.description,
            system_prompt=spec.system_prompt,
            tools=tuple(
                tools_by_name[name]
                for name in sorted(allowed_tool_names)
                if name in tools_by_name
            ),
        )
    return definitions


class AgentRegistry:
    """按 Agent Session 缓存绑定独立 Sandbox 的专业 Agent"""

    def __init__(
        self,
        definitions: Mapping[AgentType, AgentDefinition],
        agent_factory: Callable[
            [AgentSessionKey],
            Coroutine[Any, Any, CompiledStateGraph],
        ],
    ) -> None:
        """初始化专业 Agent 定义和 Session 级实例缓存"""
        definition_keys = set(definitions)
        expected_keys = set(AGENT_TYPES)
        if definition_keys != expected_keys:
            raise ValueError("智能体注册表必须包含所有专家类型的定义")
        for agent_type, definition in definitions.items():
            if definition.agent_type != agent_type:
                raise ValueError("智能体定义键与其实际 agent_type 不匹配")
        self._agent_factory = agent_factory
        self._agents: dict[str, CompiledStateGraph] = {}
        self._build_tasks: dict[str, asyncio.Task[CompiledStateGraph]] = {}
        self._lock = asyncio.Lock()

    async def get_agent(self, session_key: AgentSessionKey) -> CompiledStateGraph:
        """获取绑定当前 Session Sandbox 的专业 Agent"""
        validate_agent_type(session_key.agent_type)
        cache_key = session_key.checkpoint_ns
        async with self._lock:
            if agent := self._agents.get(cache_key):
                return agent
            build_task = self._build_tasks.get(cache_key)
            if build_task is None:
                build_task = asyncio.create_task(self._agent_factory(session_key))
                self._build_tasks[cache_key] = build_task
        try:
            agent = await asyncio.shield(build_task)
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._lock:
                if self._build_tasks.get(cache_key) is build_task:
                    self._build_tasks.pop(cache_key, None)
            raise
        async with self._lock:
            if self._build_tasks.get(cache_key) is build_task:
                self._build_tasks.pop(cache_key, None)
                self._agents[cache_key] = agent
        return agent

    def clear(self) -> None:
        """释放 Session Agent 缓存并取消未完成构建"""
        for task in self._build_tasks.values():
            task.cancel()
        self._build_tasks.clear()
        self._agents.clear()
