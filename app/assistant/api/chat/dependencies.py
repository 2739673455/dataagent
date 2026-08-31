"""聊天接口依赖。"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.repositories.conversation import ConversationPGRepo
from app.shared.clients.postgres_client_manager import (
    assistant_postgres_client_manager,
)


async def _get_conversation_pg_repo(
    session: Annotated[
        AsyncSession,
        Depends(assistant_postgres_client_manager.get_session),
    ],
) -> ConversationPGRepo:
    """创建会话目录数据访问。"""
    return ConversationPGRepo(session)


ConversationPGRepoDep = Annotated[
    ConversationPGRepo,
    Depends(_get_conversation_pg_repo),
]
