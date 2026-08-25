"""Analytics 应用服务依赖协议"""

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from app.analytics.agents.contracts import PlannerTurnContext
from app.analytics.agents.manager import ConversationAgentRuntime


class AgentRuntimeManager(Protocol):
    """聊天服务需要的最小 Agent 运行时能力"""

    async def get_conversation_runtime(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        """获取指定用户会话的 Agent 运行时"""
        ...

    def execution(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        runtime: ConversationAgentRuntime,
    ) -> AbstractAsyncContextManager[PlannerTurnContext]:
        """创建绑定 Planner 回合预算的执行上下文"""
        ...
