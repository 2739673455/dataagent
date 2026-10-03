"""语义召回快照、上下文查询、合并与资源删除协议。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.metadata.contracts import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.query.contracts import QueryExperienceRecallResult

SemanticResourceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


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


@dataclass(frozen=True, slots=True)
class SemanticRecallUpdate:
    """一次召回事务的前后快照及本次实际检索结果。"""

    record: SemanticRecallRecord
    previous: SemanticRecallRecord | None
    recalled: SemanticResourceRecallResponse


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
    def different_queries(self) -> MergeRecallsRequest:
        """校验合并来源与目标使用不同的召回业务键。"""
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
    def unique_queries(self) -> DeleteRecallsRequest:
        """校验一次删除请求中每个召回业务键只出现一次。"""
        queries = [item.query for item in self.deletions]
        if len(set(queries)) != len(queries):
            raise ValueError("同一 query 只能出现一次")
        return self
