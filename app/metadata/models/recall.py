"""语义召回记录模型"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import DateTime, Index, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.shared.contracts.query_experience import QueryExperienceRecallResult
from app.shared.database.base import MetaBase

SemanticResourceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


def normalize_semantic_recall_query(query: str) -> str:
    """校验并清理查询业务键"""
    normalized = query.strip()
    if not normalized:
        raise ValueError("query 不能为空")
    if len(normalized) > 1000:
        raise ValueError("query 长度不能超过 1000")
    return normalized


class SemanticRecallSnapshot(MetaBase):
    """语义召回持久化快照"""

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
    request: SemanticResourceRecallRequest | None
    response: SemanticResourceRecallResponse
    query_experiences: list[QueryExperienceRecallResult]
    query_experiences_retrieved_at: datetime
    query_experience_role_name: str | None
    query_experience_authorization_epoch: UUID | None
    source_queries: list[str]
    created_at: datetime


class SemanticRecallColumnDeletion(BaseModel):
    """一个字段或其部分字段值的删除选择器"""

    model_config = ConfigDict(extra="forbid")

    values: list[str] | None = Field(default=None, min_length=1)

    @property
    def deletes_entire_column(self) -> bool:
        """未指定字段值时删除整个字段"""
        return self.values is None


class SemanticRecallTableDeletion(BaseModel):
    """一张表或其中部分字段的删除选择器"""

    model_config = ConfigDict(extra="forbid")

    columns: dict[SemanticResourceName, SemanticRecallColumnDeletion] | None = (
        Field(default=None, min_length=1)
    )

    @property
    def deletes_entire_table(self) -> bool:
        """未指定字段时删除整张表"""
        return self.columns is None


class SemanticRecallQueryExperienceDeletion(BaseModel):
    """一条查询经验的删除选择器"""

    model_config = ConfigDict(extra="forbid")

    id: UUID


class SemanticRecallMetricDeletion(BaseModel):
    """一个指标的删除选择器"""

    model_config = ConfigDict(extra="forbid")


class SemanticRecallResourceDeletion(BaseModel):
    """一个 query 内待删除的语义上下文资源树"""

    model_config = ConfigDict(extra="forbid")

    query: str
    tables: dict[SemanticResourceName, SemanticRecallTableDeletion] = Field(
        default_factory=dict
    )
    metrics: dict[SemanticResourceName, SemanticRecallMetricDeletion] = Field(
        default_factory=dict
    )
    query_experiences: list[SemanticRecallQueryExperienceDeletion] = Field(
        default_factory=list
    )

    @property
    def deletes_entire_query(self) -> bool:
        """未指定资源时删除整个 query 上下文"""
        return not any(
            (
                self.tables,
                self.metrics,
                self.query_experiences,
            )
        )
