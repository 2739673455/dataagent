"""Assistant 应用服务依赖协议。"""

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from app.assistant.checkpoints.reader import CheckpointState
from app.assistant.execution.types import (
    ConversationAgentRuntime,
    DelegationActivityHistory,
)


class AgentRuntimeManager(Protocol):
    """聊天服务需要的最小 Agent 运行时能力。"""

    def use_runtime(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AbstractAsyncContextManager[ConversationAgentRuntime]:
        """借用并保护会话运行时，退出后允许缓存淘汰。"""
        ...

    async def can_resume_planner(self, user_id: int, conversation_id: UUID) -> bool:
        """只读取 Planner 的待执行任务状态。"""
        ...

    async def read_planner_state(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> CheckpointState:
        """读取 Planner 最新物化状态。"""
        ...

    async def read_delegation_activity(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        agent_type: str,
        session_id: str,
        delegation_id: str,
    ) -> DelegationActivityHistory | None:
        """读取一次 Specialist delegation 的历史活动。"""
        ...


class ConversationFileInspector(Protocol):
    """聊天消息投影所需的会话文件检查能力。"""

    async def is_downloadable_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bool:
        """检查路径是否为当前会话可下载的普通文件。"""
        ...


class ConversationLifecycleLockProvider(Protocol):
    """会话生命周期所需的跨进程锁能力。"""

    def advisory_lock(
        self,
        name: str,
    ) -> AbstractAsyncContextManager[None]:
        """创建指定名称的非阻塞跨进程锁上下文。"""
        ...


class ConversationAgentLifecycle(Protocol):
    """会话清理所需的最小 Agent 生命周期能力。"""

    async def delete_agent_under_lifecycle_lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """在生命周期锁内删除会话 Agent 状态。"""
        ...

    async def delete_user_agents(self, user_id: int) -> None:
        """删除用户全部 Agent 状态。"""
        ...


class ConversationSandboxCleaner(Protocol):
    """会话清理所需的最小沙箱能力。"""

    async def delete_conversation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """删除一个会话的沙箱资源。"""
        ...
