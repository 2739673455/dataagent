"""查询执行记录的持久化模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import QueryBase


class QueryExecution(QueryBase):
    """一次 SQL 尝试。"""

    __tablename__ = "query_executions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    experience_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("query_experiences.id", ondelete="SET NULL"),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    authorization_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    analysis_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(256))
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sql: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sql: Mapped[str | None] = mapped_column(Text)
    sql_template: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(Text)
    validation: Mapped[dict[str, object] | None] = mapped_column(JSON)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('rejected', 'failed', 'succeeded')",
            name="ck_query_execution_status",
        ),
        Index(
            "ix_query_execution_session",
            "user_id",
            "conversation_id",
            "analysis_id",
            "session_id",
        ),
    )
