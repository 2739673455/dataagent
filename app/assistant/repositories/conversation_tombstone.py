"""会话删除墓碑 PostgreSQL 数据访问"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.models.conversation_tombstone import ConversationTombstone


class ConversationTombstonePGRepo:
    """管理阻止已删除会话被跨进程任务重新创建的墓碑"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化会话墓碑数据访问"""
        self._session = session

    async def exists(self, user_id: int, conversation_id: UUID) -> bool:
        """判断会话墓碑是否存在"""
        tombstone = await self._session.scalar(
            select(ConversationTombstone).where(
                ConversationTombstone.user_id == user_id,
                ConversationTombstone.conversation_id == conversation_id,
            )
        )
        return tombstone is not None

    async def save(self, user_id: int, conversation_id: UUID) -> None:
        """幂等写入会话墓碑"""
        await self._session.execute(
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
        await self._session.flush()

    async def delete_by_user(self, user_id: int) -> None:
        """删除用户全部会话墓碑"""
        await self._session.execute(
            delete(ConversationTombstone).where(
                ConversationTombstone.user_id == user_id
            )
        )
        await self._session.flush()
