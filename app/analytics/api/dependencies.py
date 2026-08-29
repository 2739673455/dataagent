"""Analytics 模块运行时依赖"""

from typing import Annotated

from fastapi import Depends

from app.analytics.agents.manager import AgentManager
from app.analytics.services.conversation_lifecycle import ConversationLifecycleService
from app.providers import (
    agent_manager,
    conversation_lifecycle_service,
    sandbox_manager,
)
from app.sandbox.manager import DockerSandboxManager


def _get_agent_manager() -> AgentManager:
    """获取应用级 Agent 管理器"""
    return agent_manager


def _get_sandbox_manager() -> DockerSandboxManager:
    """获取应用级沙箱管理器"""
    return sandbox_manager


def _get_conversation_lifecycle_service() -> ConversationLifecycleService:
    """获取应用级会话生命周期服务"""
    return conversation_lifecycle_service


AgentManagerDep = Annotated[AgentManager, Depends(_get_agent_manager)]
SandboxManagerDep = Annotated[DockerSandboxManager, Depends(_get_sandbox_manager)]
ConversationLifecycleServiceDep = Annotated[
    ConversationLifecycleService,
    Depends(_get_conversation_lifecycle_service),
]
