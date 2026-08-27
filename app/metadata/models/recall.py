"""语义召回记录模型"""

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import DateTime, Index, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.metadata.models.search import (
    SemanticResourceSearchRequest,
    SemanticSearchResponse,
)
from app.shared.contracts.query_experience import QueryExperienceSearchResult
from app.shared.database.base import AnalyticsBase


def normalize_semantic_recall_query(query: str) -> str:
    """校验并清理查询业务键"""
    normalized = query.strip()
    if not normalized:
        raise ValueError("query 不能为空")
    if len(normalized) > 1000:
        raise ValueError("query 长度不能超过 1000")
    return normalized


class SemanticRecallSnapshot(AnalyticsBase):
    """语义召回关系快照"""

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
            "ix_semantic_recall_snapshots_conversation_created",
            "user_id",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_semantic_recall_snapshots_user",
            "user_id",
        ),
        Index(
            "ix_semantic_recall_snapshots_query_created",
            "user_id",
            "conversation_id",
            "query",
            "created_at",
        ),
    )


class SemanticRecallRecord(BaseModel):
    """一次独立检索或多次检索的合并快照"""

    model_config = ConfigDict(extra="forbid")

    user_id: int
    conversation_id: UUID
    query: str = Field(min_length=1, max_length=1000)
    request: SemanticResourceSearchRequest | None
    response: SemanticSearchResponse
    query_experiences: list[QueryExperienceSearchResult]
    query_experiences_retrieved_at: datetime
    source_queries: list[str]
    created_at: datetime
    updated_at: datetime

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: object) -> object:
        """清理召回记录的查询业务键"""
        return (
            normalize_semantic_recall_query(value) if isinstance(value, str) else value
        )

    @model_validator(mode="after")
    def validate_context_payload(self) -> Self:
        """校验持续上下文版本的数据约束"""
        if len(self.query_experiences) > 3:
            raise ValueError("召回上下文最多包含三条查询经验")
        if self.request is not None:
            if not set(self.request.terms).issubset(self.response.terms):
                raise ValueError("本次检索词必须包含在累计召回结果中")
        else:
            if not self.source_queries:
                raise ValueError("没有检索请求的召回必须包含来源 query")
        return self
