"""Planner 的 Session-aware 专业 Agent 委派工具"""

from typing import Annotated, cast

from langchain.tools import ToolRuntime, tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from app.agents.contracts import AgentType, DelegateAgentRequest
from app.agents.session_service import AgentSessionService


def create_delegate_agent_tool(service: AgentSessionService) -> BaseTool:
    """创建只绑定当前用户会话的 delegate_agent Tool"""

    @tool("delegate_agent")
    async def delegate_agent(
        runtime: ToolRuntime,
        analysis_id: Annotated[
            str,
            "分析标识，只能包含小写字母、数字、连字符和下划线，最长 64 字符",
        ],
        agent_type: Annotated[
            AgentType,
            "专业 Agent 类型",
        ],
        session_id: Annotated[
            str,
            "专业 Session 标识，首次创建后续接和修补时必须复用",
        ],
        message: Annotated[
            str,
            "交给专业 Agent 的完整目标、输入产物路径和约束",
        ],
        repair_depth: Annotated[
            int,
            "当前修补链深度，普通委派为 0，沿 RepairRequest 委派时加一",
        ] = 0,
    ) -> dict[str, object]:
        """创建或恢复专业 Agent Session 并返回可验证的结构化结果"""
        request = DelegateAgentRequest(
            analysis_id=analysis_id,
            agent_type=agent_type,
            session_id=session_id,
            message=message,
            repair_depth=repair_depth,
        )
        result = await service.delegate(
            request,
            cast(RunnableConfig, runtime.config),
        )
        return result.model_dump(mode="json")

    return delegate_agent
