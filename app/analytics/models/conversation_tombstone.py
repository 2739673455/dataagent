"""会话删除墓碑关系模型"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AnalyticsBase


class ConversationTombstone(AnalyticsBase):
    """阻止已删除会话被跨进程任务重新创建的持久化标记"""

    __tablename__ = "conversation_tombstones"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_conversation_tombstones_user", "user_id"),
    )
