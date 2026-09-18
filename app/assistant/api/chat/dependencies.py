"""聊天接口依赖。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from app.assistant.api.dependencies import (
    AgentManagerDep,
    ConversationLifecycleServiceDep,
    ConversationRunServiceDep,
)
from app.assistant.execution.turn import ConversationTurnService
from app.assistant.repositories.conversation import ConversationPGRepo
from app.dependencies import WebResourcesDep


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
    lifecycle: ConversationLifecycleServiceDep,
    runs: ConversationRunServiceDep,
    agents: AgentManagerDep,
) -> ConversationTurnService:
    """组装请求级会话回合用例。"""
    return ConversationTurnService(
        repository=repository, lifecycle=lifecycle, runs=runs, agents=agents
    )


ConversationTurnServiceDep = Annotated[
    ConversationTurnService, Depends(_get_conversation_turn_service)
]
