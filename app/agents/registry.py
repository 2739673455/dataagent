"""专业 Agent 定义与实例注册表"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.agents.anomaly_detection.prompt import ANOMALY_DETECTION_SYSTEM_PROMPT
from app.agents.attribution.prompt import ATTRIBUTION_SYSTEM_PROMPT
from app.agents.contracts import (
    AGENT_TYPES,
    AgentSessionKey,
    AgentType,
    validate_agent_type,
)
from app.agents.data_query.prompt import DATA_QUERY_SYSTEM_PROMPT
from app.agents.visualization.prompt import VISUALIZATION_SYSTEM_PROMPT

_TOOL_ALLOWLISTS: dict[AgentType, frozenset[str]] = {
    "data_query": frozenset(
        {
            "search_semantic_resources",
            "list_semantic_recalls",
            "get_semantic_recall",
            "merge_semantic_recalls",
            "delete_semantic_recalls",
            "check_sql_syntax",
            "run_readonly_sql",
        }
    ),
    "attribution": frozenset(),
    "anomaly_detection": frozenset(),
    "visualization": frozenset(),
}

_REQUIRED_TOOLS: dict[AgentType, frozenset[str]] = {
    "data_query": frozenset(
        {"search_semantic_resources", "check_sql_syntax", "run_readonly_sql"}
    ),
}

_SYSTEM_PROMPTS: dict[AgentType, str] = {
    "data_query": DATA_QUERY_SYSTEM_PROMPT,
    "attribution": ATTRIBUTION_SYSTEM_PROMPT,
    "anomaly_detection": ANOMALY_DETECTION_SYSTEM_PROMPT,
    "visualization": VISUALIZATION_SYSTEM_PROMPT,
}

_DESCRIPTIONS: dict[AgentType, str] = {
    "data_query": "检索语义目录并生成、检查、执行只读数据查询",
    "attribution": "对指标变化执行贡献分解、维度下钻和根因候选分析",
    "anomaly_detection": "检测时序异常、结构突变和数据质量问题",
    "visualization": "生成可追溯的图表、表格和下载报告",
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
        validate_agent_type(self.agent_type)
        if not self.description.strip() or not self.system_prompt.strip():
            raise ValueError("agent definition text must not be empty")
        tool_names = [tool.name for tool in self.tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError(f"duplicate tools for agent type {self.agent_type}")
        if "delegate_agent" in tool_names or "task" in tool_names:
            raise ValueError("specialist agents cannot delegate other agents")

    @property
    def tool_names(self) -> frozenset[str]:
        """返回专业 Agent 的自定义 Tool 白名单"""
        return frozenset(tool.name for tool in self.tools)


def build_agent_definitions(
    tools: Iterable[BaseTool],
) -> dict[AgentType, AgentDefinition]:
    """按工具名称白名单构造四类专业 Agent 定义"""
    tools_by_name: dict[str, BaseTool] = {}
    for tool in tools:
        if tool.name in tools_by_name:
            raise ValueError(f"duplicate tool name: {tool.name}")
        tools_by_name[tool.name] = tool

    missing_by_agent = {
        agent_type: sorted(required - tools_by_name.keys())
        for agent_type, required in _REQUIRED_TOOLS.items()
        if required - tools_by_name.keys()
    }
    if missing_by_agent:
        details = "; ".join(
            f"{agent_type}: {', '.join(names)}"
            for agent_type, names in missing_by_agent.items()
        )
        raise ValueError(f"required specialist tools are missing: {details}")

    return {
        agent_type: AgentDefinition(
            agent_type=agent_type,
            description=_DESCRIPTIONS[agent_type],
            system_prompt=_SYSTEM_PROMPTS[agent_type],
            tools=tuple(
                tools_by_name[name]
                for name in sorted(_TOOL_ALLOWLISTS[agent_type])
                if name in tools_by_name
            ),
        )
        for agent_type in AGENT_TYPES
    }


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
        definition_keys = set(definitions)
        expected_keys = set(AGENT_TYPES)
        if definition_keys != expected_keys:
            raise ValueError("registry must contain all specialist definitions")
        for agent_type, definition in definitions.items():
            if definition.agent_type != agent_type:
                raise ValueError("agent definition key does not match its agent type")
        self._definitions = dict(definitions)
        self._agent_factory = agent_factory
        self._agents: dict[str, CompiledStateGraph] = {}
        self._build_tasks: dict[str, asyncio.Task[CompiledStateGraph]] = {}
        self._lock = asyncio.Lock()

    @property
    def agent_types(self) -> tuple[AgentType, ...]:
        """返回固定顺序的已注册 Agent 类型"""
        return AGENT_TYPES

    def get_definition(self, agent_type: AgentType) -> AgentDefinition:
        """读取专业 Agent 静态定义"""
        validate_agent_type(agent_type)
        return self._definitions[agent_type]

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
