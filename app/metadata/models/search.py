"""语义索引文档、取值同步模式及元数据召回请求和响应。"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.metadata.models.catalog import ColumnReference

SemanticResourceType = Literal["column", "metric", "value"]
SemanticTextType = Literal["name", "description", "alias"]
ValueIndexSyncMode = Literal["full", "incremental"]


@dataclass(frozen=True, slots=True)
class SemanticIndexDocument:
    """一条用于全文和向量检索的索引文档。"""

    id: str
    resource_key: str
    text: str
    text_type: SemanticTextType
    embedding: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SearchHit[SearchItemT]:
    """索引命中项及原始分数。"""

    item: SearchItemT
    score: float


class SemanticResourceRecallRequest(BaseModel):
    """语义资源召回请求。"""

    terms: list[str] = Field(
        min_length=1,
        max_length=50,
        description="用于检索的业务词或同义词，至少 1 个且最多 50 个",
    )
    resource_types: list[SemanticResourceType] = Field(
        min_length=1,
        max_length=3,
        description="需要检索的字段、指标或字段值资源类型，可多选",
    )
    limit_per_type: int = Field(
        default=5, ge=1, le=20, description="每类候选的最大数量，范围 1 到 20"
    )

    @field_validator("terms")
    @classmethod
    def normalize_string_list(cls, values: list[str]) -> list[str]:
        """去除检索词首尾空白、空词及重复词，保留首次出现顺序。"""
        normalized = list(
            dict.fromkeys(value.strip() for value in values if value.strip())
        )
        if not normalized:
            raise ValueError("terms 至少需要一个非空检索词")
        return normalized

    @field_validator("resource_types")
    @classmethod
    def deduplicate_resource_types(
        cls, values: list[SemanticResourceType]
    ) -> list[SemanticResourceType]:
        """稳定去重资源类型。"""
        return list(dict.fromkeys(values))


class SemanticColumnRecallResult(BaseModel):
    """字段语义召回结果。"""

    t_name: str
    name: str
    type: str
    description: str
    alias: list[str]
    examples: list[Any]
    reference_t_name: str | None
    reference_c_name: str | None
    inclusion_reasons: list[str]
    rank_score: float | None


class SemanticMetricRecallResult(BaseModel):
    """指标语义召回结果。"""

    name: str
    description: str
    alias: list[str]
    relevant_columns: list[ColumnReference]
    rank_score: float


class SemanticValueRecallResult(BaseModel):
    """字段取值语义召回结果。"""

    value: str
    t_name: str
    c_name: str
    rank_score: float


class SemanticTableContext(BaseModel):
    """表语义上下文。"""

    name: str
    role: str
    description: str
    primary_key_columns: list[str]


class SemanticRecallFailure(BaseModel):
    """一次资源检索通道的失败范围。"""

    model_config = ConfigDict(frozen=True)

    resource_type: SemanticResourceType
    channel: Literal["fulltext", "vector"]
    term: str | None


class SemanticResourceRecallResponse(BaseModel):
    """语义目录召回响应。"""

    status: Literal["success", "partial"]
    terms: list[str]
    metrics: list[SemanticMetricRecallResult]
    columns: list[SemanticColumnRecallResult]
    values: list[SemanticValueRecallResult]
    tables: list[SemanticTableContext]
    failures: list[SemanticRecallFailure]
    warnings: list[str]
    truncated: bool
