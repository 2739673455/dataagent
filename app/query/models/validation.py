"""查询引用与 SQL 校验模型。"""

from pydantic import BaseModel, ConfigDict, Field


class QueryValidationResult(BaseModel):
    """SQL 安全检查结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    normalized_sql: str | None
    issues: list[str] = Field(default_factory=list)
