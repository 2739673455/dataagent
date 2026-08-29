"""Analytics 应用服务依赖协议"""

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from app.analytics.agents.contracts import (
    ConversationAgentRuntime,
    PlannerTurnContext,
)


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


class ConversationFileReader(Protocol):
    """聊天附件读取所需的最小沙箱能力"""

    async def download_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bytes:
        """读取会话沙箱中的文件"""
        ...


class ConversationLifecycleLockProvider(Protocol):
    """会话生命周期所需的跨进程锁能力"""

    def advisory_lock(
        self,
        name: str,
    ) -> AbstractAsyncContextManager[None]:
        """创建指定名称的非阻塞跨进程锁上下文"""
        ...


class ConversationAgentLifecycle(Protocol):
    """会话清理所需的最小 Agent 生命周期能力"""

    async def cancel_agent_execution(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """取消会话中正在执行的 Agent"""
        ...

    async def delete_agent_under_lifecycle_lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """在生命周期锁内删除会话 Agent 状态"""
        ...

    async def delete_user_agents(self, user_id: int) -> None:
        """删除用户全部 Agent 状态"""
        ...


class ConversationSandboxCleaner(Protocol):
    """会话清理所需的最小沙箱能力"""

    async def delete_conversation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """删除一个会话的沙箱资源"""
        ...


class ConversationRecallCleaner(Protocol):
    """会话清理所需的语义召回删除能力"""

    async def delete_all(self, user_id: int, conversation_id: UUID) -> None:
        """删除一个会话的全部召回记录"""
        ...

    async def delete_all_by_user(self, user_id: int) -> None:
        """删除一个用户的全部召回记录"""
        ...
