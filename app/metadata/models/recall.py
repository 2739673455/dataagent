"""语义召回记录模型"""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import DateTime, Index, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.metadata.models.search import SemanticSearchRequest, SemanticSearchResponse
from app.shared.database.base import AnalyticsBase

SemanticRecallKind = Literal["search", "merged"]


class SemanticRecallSnapshot(AnalyticsBase):
    """语义召回关系快照"""

    __tablename__ = "semantic_recall_snapshots"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    recall_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_recall_ids: Mapped[list[str]] = mapped_column(
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
            "ix_semantic_recall_snapshots_conversation_created",
            "user_id",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_semantic_recall_snapshots_user",
            "user_id",
        ),
    )


class SemanticRecallRecord(BaseModel):
    """一次独立检索或多次检索的合并快照"""

    model_config = ConfigDict(extra="forbid")

    recall_id: str
    user_id: int
    conversation_id: UUID
    kind: SemanticRecallKind
    request: SemanticSearchRequest | None
    response: SemanticSearchResponse
    source_recall_ids: list[str]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_kind_payload(self) -> Self:
        """校验原始召回和合并召回的数据约束"""
        if self.response.search_id != self.recall_id:
            raise ValueError("response.search_id 必须与 recall_id 一致")
        if self.kind == "search":
            if self.request is None:
                raise ValueError("原始检索召回必须包含 request")
            if self.source_recall_ids:
                raise ValueError("原始检索召回不能包含来源召回")
        else:
            if self.request is not None:
                raise ValueError("合并召回不能包含 request")
            if len(self.source_recall_ids) < 2:
                raise ValueError("合并召回至少需要两个来源召回")
        return self
