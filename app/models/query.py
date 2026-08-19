"""受控只读查询协议"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QueryDialect = Literal["doris", "mysql"]


class QueryTableRef(BaseModel):
    """查询引用的数据表"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str | None = None
    name: str

    @property
    def qualified_name(self) -> str:
        """返回包含数据库名的表标识"""
        return f"{self.database}.{self.name}" if self.database else self.name


class QueryColumnRef(BaseModel):
    """查询引用的物理字段"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str | None = None
    table: str
    name: str

    @property
    def qualified_name(self) -> str:
        """返回包含数据库名和表名的字段标识"""
        prefix = f"{self.database}." if self.database else ""
        return f"{prefix}{self.table}.{self.name}"


class QueryValidationIssue(BaseModel):
    """一项确定性的 SQL 校验问题"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    table: str | None = None
    column: str | None = None


class QueryValidationResult(BaseModel):
    """SQL 安全检查结果"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    dialect: QueryDialect
    normalized_sql: str | None
    tables: list[QueryTableRef] = Field(default_factory=list)
    columns: list[QueryColumnRef] = Field(default_factory=list)
    output_columns: list[str] = Field(default_factory=list)
    issues: list[QueryValidationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> "QueryValidationResult":
        """保证校验状态和问题列表一致"""
        if self.valid == bool(self.issues):
            raise ValueError("valid must be the inverse of issues")
        if self.valid and self.normalized_sql is None:
            raise ValueError("valid query requires normalized_sql")
        return self


class QueryExecutionLimits(BaseModel):
    """Doris 单次查询资源限制"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workload_group: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    timeout_seconds: int = Field(gt=0)
    memory_limit_bytes: int = Field(gt=0)
    max_scan_rows: int = Field(gt=0)
    max_scan_bytes: int = Field(gt=0)
    max_cell_bytes: int = Field(gt=0)
    max_rows: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    batch_size: int = Field(gt=0)
    sample_rows: int = Field(default=5, ge=0, le=100)
    output_format: Literal["csv"] = "csv"


@dataclass(frozen=True, slots=True)
class QueryBatch:
    """Doris 服务端游标返回的一批结果"""

    column_names: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


class QueryResultColumn(BaseModel):
    """查询结果字段信息"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str
    nullable: bool


class QueryTimeRange(BaseModel):
    """时间字段在结果集中的取值范围"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: str
    end: str


class AnalysisQueryResult(BaseModel):
    """写入会话沙盒后的查询结果摘要"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        serialize_by_alias=True,
    )

    path: str
    result_schema: list[QueryResultColumn] = Field(alias="schema")
    row_count: int
    time_range: dict[str, QueryTimeRange]
    sample: list[dict[str, Any]]

    @property
    def schema(self) -> list[QueryResultColumn]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """返回查询结果字段信息"""
        return self.result_schema
