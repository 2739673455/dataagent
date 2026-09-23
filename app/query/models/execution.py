"""查询执行配置与结果模型。"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class AnalysisQueryResult(BaseModel):
    """写入会话沙箱后的查询结果摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    columns: list[str]
    row_count: int
    sample: list[dict[str, Any]]
