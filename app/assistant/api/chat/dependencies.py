"""聊天接口依赖。"""

from typing import Annotated

from fastapi import Depends

from app.assistant.api.dependencies import (
    AgentStateReaderDep,
    ConversationLifecycleServiceDep,
    ConversationPGRepoDep,
    ConversationRunServiceDep,
    SandboxManagerDep,
)
from app.assistant.conversations.service import ConversationService
from app.assistant.conversations.turns import ConversationTurnService


def _get_conversation_turn_service(
    repository: ConversationPGRepoDep,
    runs: ConversationRunServiceDep,
    state_reader: AgentStateReaderDep,
) -> ConversationTurnService:
    """组装请求级会话回合用例。"""
    return ConversationTurnService(
        repository=repository, runs=runs, state_reader=state_reader
    )


ConversationTurnServiceDep = Annotated[
    ConversationTurnService, Depends(_get_conversation_turn_service)
]


def _get_conversation_service(
    repository: ConversationPGRepoDep,
    runs: ConversationRunServiceDep,
    state_reader: AgentStateReaderDep,
    sandbox: SandboxManagerDep,
    lifecycle: ConversationLifecycleServiceDep,
) -> ConversationService:
    """组装会话目录、历史读取和生命周期管理用例。"""
    return ConversationService(repository, runs, state_reader, sandbox, lifecycle)


ConversationServiceDep = Annotated[
    ConversationService, Depends(_get_conversation_service)
]
