"""会话目录关系模型"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AssistantBase


class Conversation(AssistantBase):
    """助手会话目录"""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    title_pending: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    title_source: Mapped[str | None] = mapped_column(Text)
    title_generation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    is_draft: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    create_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    update_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_conversations_user_update", "user_id", "update_at"),
        Index(
            "ix_conversations_expired_drafts",
            "update_at",
            postgresql_where=text("is_draft AND deletion_requested_at IS NULL"),
        ),
        Index(
            "ix_conversations_pending_deletions",
            "deletion_requested_at",
            postgresql_where=text("deletion_requested_at IS NOT NULL"),
        ),
        Index(
            "ix_conversations_pending_titles",
            "title_generation_requested_at",
            postgresql_where=text(
                "title_pending AND title_source IS NOT NULL "
                "AND deletion_requested_at IS NULL"
            ),
        ),
    )
