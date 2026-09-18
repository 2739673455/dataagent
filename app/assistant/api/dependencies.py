"""Assistant 模块运行时依赖。"""

from typing import Annotated

from fastapi import Depends

from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime
from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.execution.manager import AgentManager
from app.assistant.execution.run import ConversationRunService
from app.dependencies import WebResourcesDep
from app.sandbox.manager import DockerSandboxManager


def _get_agent_manager(resources: WebResourcesDep) -> AgentManager:
    """获取应用级 Agent 管理器。"""
    return resources.agents


def _get_sandbox_manager(resources: WebResourcesDep) -> DockerSandboxManager:
    """获取应用级沙箱管理器。"""
    return resources.sandbox


def _get_conversation_lifecycle_service(
    resources: WebResourcesDep,
) -> ConversationLifecycleService:
    """获取应用级会话生命周期服务。"""
    return resources.conversations


def _get_conversation_run_service(resources: WebResourcesDep) -> ConversationRunService:
    """获取应用级 Conversation Run 管理器。"""
    return resources.runs


AgentManagerDep = Annotated[AgentManager, Depends(_get_agent_manager)]
SandboxManagerDep = Annotated[DockerSandboxManager, Depends(_get_sandbox_manager)]
ConversationLifecycleServiceDep = Annotated[
    ConversationLifecycleService,
    Depends(_get_conversation_lifecycle_service),
]
ConversationRunServiceDep = Annotated[
    ConversationRunService,
    Depends(_get_conversation_run_service),
]


def _get_recall_runtime(resources: WebResourcesDep) -> SemanticRecallRuntime:
    """获取当前应用的召回能力资源。"""
    return resources.recall


SemanticRecallRuntimeDep = Annotated[
    SemanticRecallRuntime, Depends(_get_recall_runtime)
]
