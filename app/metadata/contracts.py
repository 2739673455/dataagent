"""元数据目录、语义检索与变更的公开数据契约。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.shared.tasks.submission import TaskSubmission

type ColumnKey = tuple[str, str]
SemanticResourceType = Literal["column", "metric", "value"]
SemanticIndexStatus = Literal["current", "stale", "missing"]
SemanticTextType = Literal["name", "description", "alias"]
SemanticMatchType = Literal["fulltext", "vector"]
ValueIndexSyncMode = Literal["full", "incremental", "clear"]
RequestedValueIndexSyncMode = Literal["full", "incremental"]


class ColumnReference(TypedDict):
    """字段联合主键引用。"""

    t_name: str
    c_name: str


@dataclass(frozen=True, slots=True)
class QueryCatalogColumn:
    """SQL 解析需要的字段定义，不携带 ORM 状态。"""

    t_name: str
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class QueryCatalogSnapshot:
    """用于单次 SQL 校验的目录快照。"""

    table_names: tuple[str, ...]
    columns: tuple[QueryCatalogColumn, ...]


@dataclass(frozen=True, slots=True)
class AssetVersions:
    """当前存在的表、字段版本；缺失的键表示资产已不存在。"""

    tables: dict[str, int]
    columns: dict[ColumnKey, int]


@dataclass(frozen=True)
class MetadataChanges:
    """区分需失效的已有资产和需同步的新版本；删除资产只参与失效。"""

    invalidated_tables: tuple[str, ...] = ()
    invalidated_columns: tuple[ColumnKey, ...] = ()
    sync_columns: tuple[ColumnKey, ...] = ()
    sync_metrics: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetadataChangeTasks:
    """变更处理提交的字段、指标同步任务，供接口返回任务标识。"""

    columns: TaskSubmission | None = None
    metrics: TaskSubmission | None = None


class MetadataChangeHandler(Protocol):
    """目录变更提交后调用的应用契约。"""

    async def handle(self, changes: MetadataChanges) -> MetadataChangeTasks:
        """处理已提交的目录变更，返回提交的字段与指标同步任务。"""
        ...


@dataclass(frozen=True, slots=True)
class SemanticIndexDocument:
    """一条可差量比较的语义索引文档。"""

    id: str
    resource_key: str
    text: str
    text_type: SemanticTextType
    embedding: list[float] | None
    embedding_revision: str
    meta_version: int
    payload_hash: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SemanticIndexDelta:
    """一个元数据资源的语义索引变更集。"""

    create: list[SemanticIndexDocument]
    update: list[SemanticIndexDocument]
    delete_ids: list[str]
    unchanged_count: int


@dataclass(frozen=True, slots=True)
class SemanticIndexSyncResult:
    """语义索引差量同步统计。"""

    created_count: int
    updated_count: int
    deleted_count: int
    embedded_count: int
    unchanged_count: int
    target_version: int
    version_committed: bool


@dataclass(frozen=True, slots=True)
class ValueIndexSyncResult:
    """取值索引水位同步统计。"""

    mode: ValueIndexSyncMode
    read_value_count: int
    upserted_count: int
    removed_count: int
    cursor_value: Any | None
    sync_generation: str | None


class SemanticResourceRecallRequest(BaseModel):
    """语义资源召回请求。"""

    model_config = ConfigDict(extra="forbid")

    terms: list[str] = Field(min_length=1, max_length=20)
    resource_types: list[SemanticResourceType] = Field(
        min_length=1,
        max_length=3,
    )
    limit_per_type: int = Field(default=5, ge=1, le=20)

    @field_validator("terms")
    @classmethod
    def normalize_string_list(cls, values: list[str]) -> list[str]:
        """清理并稳定去重字符串列表。"""
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


class SemanticMatchReason(BaseModel):
    """一次索引命中的结构化依据。"""

    model_config = ConfigDict(frozen=True)

    match_type: SemanticMatchType
    term: str
    score: float


class SemanticRecallFailure(BaseModel):
    """一次资源检索通道的失败范围。"""

    model_config = ConfigDict(frozen=True)

    resource_type: SemanticResourceType
    channel: SemanticMatchType
    term: str | None


class SemanticMetricRecallResult(BaseModel):
    """指标语义召回结果。"""

    name: str
    description: str
    alias: list[str]
    relevant_columns: list[ColumnReference]
    rank_score: float
    match_reasons: list[SemanticMatchReason]
    meta_version: int
    index_version: int
    index_status: SemanticIndexStatus


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
    match_reasons: list[SemanticMatchReason]
    meta_version: int
    index_version: int
    index_status: SemanticIndexStatus


class SemanticValueRecallResult(BaseModel):
    """字段取值语义召回结果。"""

    value: str
    t_name: str
    c_name: str
    rank_score: float
    match_reasons: list[SemanticMatchReason]
    sync_status: Literal["syncing", "succeeded", "failed"] | None
    synced_at: datetime | None


class SemanticTableContext(BaseModel):
    """表语义上下文。"""

    name: str
    role: str
    description: str
    primary_key_columns: list[str]
    meta_version: int


class SemanticResourceRecallResponse(BaseModel):
    """语义目录召回响应。"""

    status: Literal["success", "partial"]
    recall_id: str
    terms: list[str]
    metrics: list[SemanticMetricRecallResult]
    columns: list[SemanticColumnRecallResult]
    values: list[SemanticValueRecallResult]
    tables: list[SemanticTableContext]
    failures: list[SemanticRecallFailure]
    warnings: list[str]
    truncated: bool


def column_reference_key(reference: ColumnReference) -> ColumnKey:
    """将字段引用转换为可用于集合和映射的联合键。"""
    return reference["t_name"], reference["c_name"]


def column_key_reference(key: ColumnKey) -> ColumnReference:
    """将字段联合键转换为 JSON 可序列化的字段引用。"""
    return ColumnReference(t_name=key[0], c_name=key[1])
