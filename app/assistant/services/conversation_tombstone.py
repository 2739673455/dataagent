"""会话删除墓碑服务。"""

from uuid import UUID

from app.assistant.repositories.conversation_tombstone import (
    ConversationTombstonePGRepo,
)
from app.shared.clients.postgres_client_manager import PostgresClientManager


class ConversationTombstoneService:
    """为 Agent 生命周期提供短事务会话墓碑操作。"""

    def __init__(self, postgres: PostgresClientManager) -> None:
        """初始化会话墓碑服务。"""
        self._postgres = postgres

    async def exists(self, user_id: int, conversation_id: UUID) -> bool:
        """判断会话墓碑是否存在。"""
        async with self._postgres.session() as session:
            return await ConversationTombstonePGRepo(session).exists(
                user_id,
                conversation_id,
            )

    async def save(self, user_id: int, conversation_id: UUID) -> None:
        """幂等写入会话墓碑。"""
        async with self._postgres.session() as session, session.begin():
            await ConversationTombstonePGRepo(session).save(
                user_id,
                conversation_id,
            )

    async def delete_by_user(self, user_id: int) -> None:
        """删除用户全部会话墓碑。"""
        async with self._postgres.session() as session, session.begin():
            await ConversationTombstonePGRepo(session).delete_by_user(user_id)
