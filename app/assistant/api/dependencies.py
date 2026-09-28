"""Assistant 模块运行时依赖。"""

from typing import Annotated

from fastapi import Depends

from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.execution.manager import AgentManager
from app.assistant.execution.run import ConversationRunService
from app.assistant.tasks import ConversationTasks
from app.dependencies import WebResourcesDep
from app.sandbox.manager import DockerSandboxManager


def _get_sandbox_manager(resources: WebResourcesDep) -> DockerSandboxManager:
    """获取应用级沙箱管理器。"""
    return resources.sandbox


SandboxManagerDep = Annotated[DockerSandboxManager, Depends(_get_sandbox_manager)]


def _get_agent_manager(resources: WebResourcesDep) -> AgentManager:
    """获取应用级 Agent 管理器。"""
    return resources.agents


AgentManagerDep = Annotated[AgentManager, Depends(_get_agent_manager)]


def _get_conversation_run_service(resources: WebResourcesDep) -> ConversationRunService:
    """获取应用级 Conversation Run 管理器。"""
    return resources.runs


ConversationRunServiceDep = Annotated[
    ConversationRunService,
    Depends(_get_conversation_run_service),
]


def _get_conversation_lifecycle_service(
    resources: WebResourcesDep,
) -> ConversationLifecycleService:
    """获取应用级会话生命周期服务。"""
    return resources.conversations


ConversationLifecycleServiceDep = Annotated[
    ConversationLifecycleService,
    Depends(_get_conversation_lifecycle_service),
]


def _get_conversation_tasks(resources: WebResourcesDep) -> ConversationTasks:
    """获取当前应用的会话后台任务服务。"""
    return resources.tasks


ConversationTasksDep = Annotated[ConversationTasks, Depends(_get_conversation_tasks)]
