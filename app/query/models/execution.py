"""查询执行配置、结果与持久化模型"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import MetaBase

type QueryExecutionStatus = Literal["rejected", "failed", "succeeded"]


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
    max_rows: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)


class QueryExecutionOptions(BaseModel):
    """Doris 查询流式处理与结果摘要选项"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_size: int = Field(gt=0)
    sample_rows: int = Field(default=5, ge=0, le=100)


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
    """写入会话沙箱后的查询结果摘要"""

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


class QueryExecution(MetaBase):
    """一次 SQL 尝试"""

    __tablename__ = "query_executions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    experience_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("query_experiences.id", ondelete="SET NULL"),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    authorization_epoch: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    analysis_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(256))
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sql: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sql: Mapped[str | None] = mapped_column(Text)
    sql_template: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(Text)
    validation: Mapped[dict[str, object] | None] = mapped_column(JSON)
    plan_estimate: Mapped[dict[str, object] | None] = mapped_column(JSON)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('rejected', 'failed', 'succeeded')",
            name="ck_query_execution_status",
        ),
        Index(
            "ix_query_execution_session",
            "user_id",
            "conversation_id",
            "analysis_id",
            "session_id",
        ),
    )
