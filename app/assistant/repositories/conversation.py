"""PostgreSQL 会话目录数据访问。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.models.conversation import Conversation


class ConversationPGRepo:
    """使用关系表存储会话目录。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前操作使用的异步数据库会话。"""
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """返回当前数据访问绑定的数据库会话。"""
        return self._session

    async def create(
        self,
        user_id: int,
        title: str,
        *,
        is_draft: bool = False,
    ) -> Conversation:
        """创建会话目录信息。"""
        now = datetime.now(UTC)
        conversation = Conversation(
            user_id=user_id,
            title=title,
            is_draft=is_draft,
            create_at=now,
            update_at=now,
        )
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        include_deleting: bool = False,
    ) -> Conversation | None:
        """获取当前用户的会话目录信息。"""
        statement = select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.id == conversation_id,
        )
        if not include_deleting:
            statement = statement.where(Conversation.deletion_requested_at.is_(None))
        return await self._session.scalar(statement)

    async def update(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        title: str | None = None,
        is_draft: bool | None = None,
        deletion_requested_at: datetime | None = None,
    ) -> None:
        """更新当前用户未删除会话的指定字段和最后活动时间。"""
        values: dict[str, str | bool | datetime] = {"update_at": datetime.now(UTC)}
        if title is not None:
            values["title"] = title
        if is_draft is not None:
            values["is_draft"] = is_draft
        if deletion_requested_at is not None:
            values["deletion_requested_at"] = deletion_requested_at
        await self._session.execute(
            update(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.id == conversation_id,
                Conversation.deletion_requested_at.is_(None),
            )
            .values(**values)
        )
        await self._session.flush()

    async def list_by_user(self, user_id: int) -> list[Conversation]:
        """按最后活动时间倒序获取用户的正式会话。"""
        result = await self._session.scalars(
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.is_draft.is_(False),
                Conversation.deletion_requested_at.is_(None),
            )
            .order_by(Conversation.update_at.desc(), Conversation.id.desc())
        )
        return list(result)

    async def list_expired_drafts(
        self,
        cutoff: datetime,
        *,
        limit: int,
    ) -> list[Conversation]:
        """跨用户列出最后活动时间已过期的草稿。"""
        result = await self._session.scalars(
            select(Conversation)
            .where(
                Conversation.is_draft.is_(True),
                Conversation.deletion_requested_at.is_(None),
                Conversation.update_at <= cutoff,
            )
            .order_by(Conversation.update_at, Conversation.id)
            .limit(limit)
        )
        return list(result)

    async def list_pending_deletions(self, *, limit: int) -> list[Conversation]:
        """跨用户列出已写入墓碑且待物理清理的会话。"""
        result = await self._session.scalars(
            select(Conversation)
            .where(Conversation.deletion_requested_at.is_not(None))
            .order_by(Conversation.deletion_requested_at, Conversation.id)
            .limit(limit)
        )
        return list(result)

    async def delete(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话目录信息。"""
        await self._session.execute(
            delete(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.id == conversation_id,
            )
        )
        await self._session.flush()
