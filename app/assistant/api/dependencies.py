"""Assistant 模块运行时依赖。"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends

from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.execution.runs import ConversationRunService
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.sessions.state_reader import AgentStateReader
from app.dependencies import WebResourcesDep
from app.sandbox import DockerSandboxManager


def _get_agent_state_reader(resources: WebResourcesDep) -> AgentStateReader:
    """获取只读状态查询服务。"""
    return resources.agent_state


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


AgentStateReaderDep = Annotated[AgentStateReader, Depends(_get_agent_state_reader)]
SandboxManagerDep = Annotated[DockerSandboxManager, Depends(_get_sandbox_manager)]
ConversationLifecycleServiceDep = Annotated[
    ConversationLifecycleService,
    Depends(_get_conversation_lifecycle_service),
]
ConversationRunServiceDep = Annotated[
    ConversationRunService,
    Depends(_get_conversation_run_service),
]


async def _get_conversation_pg_repo(
    resources: WebResourcesDep,
) -> AsyncGenerator[ConversationPGRepo]:
    """创建会话目录数据访问。"""
    async with resources.assistant.session() as session:
        yield ConversationPGRepo(session)


ConversationPGRepoDep = Annotated[
    ConversationPGRepo,
    Depends(_get_conversation_pg_repo),
]
