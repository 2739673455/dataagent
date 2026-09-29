"""Assistant HTTP 接口的应用资源与请求级服务依赖。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from app.assistant.agents.manager import AgentManager
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.conversation import (
    AttachmentService,
    ConversationLifecycleService,
    ConversationTurnService,
)
from app.assistant.services.run import ConversationRunService
from app.assistant.services.tasks import ConversationTasks
from app.dependencies import WebResourcesDep
from app.sandbox import DockerSandboxManager


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


async def _get_conversation_pg_repo(
    resources: WebResourcesDep,
) -> AsyncIterator[ConversationPGRepo]:
    """创建会话目录数据访问。"""
    async with resources.assistant.session() as session:
        yield ConversationPGRepo(session)


ConversationPGRepoDep = Annotated[
    ConversationPGRepo,
    Depends(_get_conversation_pg_repo),
]


def _get_conversation_turn_service(
    repository: ConversationPGRepoDep,
    runs: ConversationRunServiceDep,
    agents: AgentManagerDep,
    tasks: ConversationTasksDep,
) -> ConversationTurnService:
    """组装请求级会话回合用例。"""
    return ConversationTurnService(
        repository=repository, runs=runs, agents=agents, tasks=tasks
    )


ConversationTurnServiceDep = Annotated[
    ConversationTurnService, Depends(_get_conversation_turn_service)
]


def _get_attachment_service(
    repository: ConversationPGRepoDep,
    sandbox: SandboxManagerDep,
) -> AttachmentService:
    """绑定当前请求的附件服务资源。"""
    return AttachmentService(repository, sandbox)


AttachmentServiceDep = Annotated[AttachmentService, Depends(_get_attachment_service)]
