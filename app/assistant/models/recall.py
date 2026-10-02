"""语义召回记录模型。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AssistantBase


class SemanticRecallSnapshot(AssistantBase):
    """语义召回持久化快照。"""

    __tablename__ = "semantic_recall_snapshots"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    recall_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    query: Mapped[str] = mapped_column(String(1000), nullable=False)
    request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_queries: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    __table_args__ = (
        Index(
            "ix_semantic_recall_snapshots_conversation_updated",
            "user_id",
            "conversation_id",
            "updated_at",
        ),
        Index(
            "ix_semantic_recall_snapshots_user",
            "user_id",
        ),
        Index(
            "ix_semantic_recall_snapshots_query_updated",
            "user_id",
            "conversation_id",
            "query",
            "updated_at",
        ),
    )
