"""会话删除墓碑的短事务存储。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.assistant.models.conversation_tombstone import ConversationTombstone
from app.shared.clients.postgres_client_manager import PostgresClientManager


class ConversationTombstoneStore:
    """管理阻止已删除会话被跨进程任务重建的墓碑。"""

    def __init__(self, postgres: PostgresClientManager) -> None:
        """绑定 Assistant 数据库短事务入口。"""
        self._postgres = postgres

    async def exists(self, user_id: int, conversation_id: UUID) -> bool:
        """判断会话墓碑是否存在。"""
        async with self._postgres.session() as session:
            tombstone = await session.scalar(
                select(ConversationTombstone).where(
                    ConversationTombstone.user_id == user_id,
                    ConversationTombstone.conversation_id == conversation_id,
                )
            )
        return tombstone is not None

    async def save(self, user_id: int, conversation_id: UUID) -> None:
        """幂等写入会话墓碑。"""
        async with self._postgres.session() as session, session.begin():
            await session.execute(
                insert(ConversationTombstone)
                .values(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    deleted_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        ConversationTombstone.user_id,
                        ConversationTombstone.conversation_id,
                    ]
                )
            )

    async def delete_by_user(self, user_id: int) -> None:
        """删除用户全部会话墓碑。"""
        async with self._postgres.session() as session, session.begin():
            await session.execute(
                delete(ConversationTombstone).where(
                    ConversationTombstone.user_id == user_id
                )
            )
