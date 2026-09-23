"""查询执行配置与结果模型。"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.shared.contracts.doris import DORIS_WORKLOAD_GROUP_PATTERN


class QueryExecutionTimeoutError(RuntimeError):
    """Doris 查询执行超时。"""


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
