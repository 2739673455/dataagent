"""语义召回记录模型。"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
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


class SemanticRecallSnapshot(MetaBase):
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


class SemanticRecallRecord(BaseModel):
    """一次独立检索或多次检索的合并快照。"""

    model_config = ConfigDict(extra="forbid")

    user_id: int
    conversation_id: UUID
    query: str = Field(min_length=1, max_length=1000)
    request: SemanticResourceRecallRequest | None
    response: SemanticResourceRecallResponse
    query_experiences: list[QueryExperienceRecallResult]
    query_experiences_retrieved_at: datetime
    query_experience_role_name: str | None
    query_experience_authorization_fingerprint: str | None
    source_queries: list[str]
    created_at: datetime
    updated_at: datetime


class SemanticRecallColumnDeletion(BaseModel):
    """一个字段或其部分字段值的删除选择器。"""

    model_config = ConfigDict(extra="forbid")

    values: list[str] | None = Field(default=None, min_length=1)

    @property
    def deletes_entire_column(self) -> bool:
        """未指定字段值时删除整个字段。"""
        return self.values is None


class SemanticRecallTableDeletion(BaseModel):
    """一张表或其中部分字段的删除选择器。"""

    model_config = ConfigDict(extra="forbid")

    columns: dict[SemanticResourceName, SemanticRecallColumnDeletion] | None = Field(
        default=None, min_length=1
    )

    @property
    def deletes_entire_table(self) -> bool:
        """未指定字段时删除整张表。"""
        return self.columns is None


class SemanticRecallQueryExperienceDeletion(BaseModel):
    """一条查询经验的删除选择器。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID


class SemanticRecallMetricDeletion(BaseModel):
    """一个指标的删除选择器。"""

    model_config = ConfigDict(extra="forbid")


class SemanticRecallResourceDeletion(BaseModel):
    """一个 query 内待删除的语义上下文资源树。"""

    model_config = ConfigDict(extra="forbid")

    query: SemanticResourceName = Field(
        description=(
            "待删除资源所属的稳定 query 业务键，必须与 recall_context 使用的 query "
            "完全一致"
        )
    )
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
        """未指定资源时删除整个 query 上下文。"""
        return not any(
            (
                self.tables,
                self.metrics,
                self.query_experiences,
            )
        )


class RecallContextRequest(SemanticResourceRecallRequest):
    """按稳定业务键补充检索并累计召回上下文。"""

    query: SemanticResourceName = Field(
        description="当前会话的稳定业务键；后续补充检索原样复用，只调整 terms 和 resource_types"
    )


class ListRecallsRequest(BaseModel):
    """读取最近召回记录。"""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=100, description="返回最近记录的数量")


class GetRecallRequest(BaseModel):
    """按稳定业务键读取召回记录。"""

    model_config = ConfigDict(extra="forbid")
    query: SemanticResourceName = Field(
        description="与 recall_context 完全一致的 query"
    )


class MergeRecallsRequest(BaseModel):
    """将来源上下文合并到目标。"""

    model_config = ConfigDict(extra="forbid")
    target_query: SemanticResourceName = Field(
        description="接收累计结果并保留的目标 query"
    )
    source_query: SemanticResourceName = Field(
        description="提供结果并在合并后删除的来源 query"
    )

    @model_validator(mode="after")
    def different_queries(self) -> "MergeRecallsRequest":
        if self.target_query == self.source_query:
            raise ValueError("目标 query 和来源 query 不能相同")
        return self


class DeleteRecallsRequest(BaseModel):
    """删除上下文或其中的资源；同一业务键仅能出现一次。"""

    model_config = ConfigDict(extra="forbid")
    deletions: list[SemanticRecallResourceDeletion] = Field(
        min_length=1, description="未提供资源选择器时删除整个 query"
    )

    @model_validator(mode="after")
    def unique_queries(self) -> "DeleteRecallsRequest":
        queries = [item.query for item in self.deletions]
        if len(set(queries)) != len(queries):
            raise ValueError("同一 query 只能出现一次")
        return self
