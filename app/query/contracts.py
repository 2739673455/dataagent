"""查询校验、执行和经验检索的公开值对象。"""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.contracts.doris import DORIS_WORKLOAD_GROUP_PATTERN

type QueryExecutionStatus = Literal["rejected", "failed", "succeeded"]
type QueryKind = Literal["business", "catalog"]
type QueryAssetKind = Literal["table", "column"]
QUERY_EXPERIENCE_RECALL_LIMIT = 3
QueryExperienceRecallStatus = Literal["success", "partial", "failed"]


class QueryTableRef(BaseModel):
    """查询引用的数据表。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str | None = None
    name: str

    @property
    def qualified_name(self) -> str:
        """返回包含数据库名的表标识。"""
        return f"{self.database}.{self.name}" if self.database else self.name


class QueryColumnRef(BaseModel):
    """查询引用的物理字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str | None = None
    table: str
    name: str

    @property
    def qualified_name(self) -> str:
        """返回包含数据库名和表名的字段标识。"""
        prefix = f"{self.database}." if self.database else ""
        return f"{prefix}{self.table}.{self.name}"


class QueryValidationIssue(BaseModel):
    """一项确定性的 SQL 校验问题。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    table: str | None = None
    column: str | None = None


class QueryValidationResult(BaseModel):
    """SQL 安全检查结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    normalized_sql: str | None
    query_kind: QueryKind = "business"
    tables: list[QueryTableRef] = Field(default_factory=list)
    columns: list[QueryColumnRef] = Field(default_factory=list)
    output_columns: list[str] = Field(default_factory=list)
    issues: list[QueryValidationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> "QueryValidationResult":
        """保证校验状态和问题列表一致。"""
        if self.valid == bool(self.issues):
            raise ValueError("valid 必须与 issues 是否为空保持相反状态")
        if self.valid and self.normalized_sql is None:
            raise ValueError("有效查询必须包含 normalized_sql")
        return self


class QueryExecutionLimits(BaseModel):
    """Doris 单次查询资源限制。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workload_group: str = Field(
        min_length=1,
        max_length=128,
        pattern=DORIS_WORKLOAD_GROUP_PATTERN,
    )
    timeout_seconds: int = Field(gt=0)
    memory_limit_bytes: int = Field(gt=0)


class QueryExecutionOptions(BaseModel):
    """Doris 查询流式处理与结果摘要选项。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_size: int = Field(gt=0)
    sample_rows: int = Field(default=5, ge=0, le=100)


@dataclass(frozen=True, slots=True)
class QueryBatch:
    """Doris 服务端游标返回的一批结果。"""

    column_names: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


class QueryResultColumn(BaseModel):
    """查询结果字段信息。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str
    nullable: bool


class QueryTimeRange(BaseModel):
    """时间字段在结果集中的取值范围。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: str
    end: str


class AnalysisQueryResult(BaseModel):
    """写入会话沙箱后的查询结果摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    columns: list[QueryResultColumn]
    row_count: int
    time_range: dict[str, QueryTimeRange]
    sample: list[dict[str, Any]]


class QueryAssetSnapshot(BaseModel):
    """查询经验返回的资产引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QueryAssetKind
    database: str
    table: str
    column: str | None = None
    meta_version: int


class QueryExperienceRecallResult(BaseModel):
    """提供给 Explorer 的紧凑查询经验。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    purpose: str
    sql_template: str
    assets: list[QueryAssetSnapshot]


class QueryExperienceRecall(BaseModel):
    """一次查询经验召回的结果及检索通道状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: QueryExperienceRecallStatus
    results: list[QueryExperienceRecallResult]
