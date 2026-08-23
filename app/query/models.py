"""受控查询领域模型与协议"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import MetaBase

QueryDialect = Literal["doris", "mysql"]
type QueryExecutionStatus = Literal["rejected", "failed", "succeeded"]
type QueryExperienceQuality = Literal["candidate", "promoted", "disabled"]
type QueryAssetKind = Literal["table", "column"]


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
            raise ValueError("valid 必须与 issues 是否为空保持相反状态")
        if self.valid and self.normalized_sql is None:
            raise ValueError("有效查询必须包含 normalized_sql")
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


class QueryExperience(MetaBase):
    """按 SQL 结构聚合的用户私有查询经验"""

    __tablename__ = "query_experiences"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dialect: Mapped[str] = mapped_column(String(16), nullable=False)
    purposes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    representative_sql: Mapped[str] = mapped_column(Text, nullable=False)
    sql_template: Mapped[str] = mapped_column(Text, nullable=False)
    quality: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="candidate",
        server_default="candidate",
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    adopted_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    indexed_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    first_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    assets: Mapped[list["QueryExperienceAsset"]] = relationship(
        back_populates="experience",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "role_name",
            "fingerprint",
            name="uq_query_experience_owner_role_fingerprint",
        ),
        CheckConstraint(
            "quality IN ('candidate', 'promoted', 'disabled')",
            name="ck_query_experience_quality",
        ),
        CheckConstraint(
            "success_count > 0 AND adopted_count >= 0",
            name="ck_query_experience_counts",
        ),
        CheckConstraint(
            "revision > 0 AND indexed_revision >= 0",
            name="ck_query_experience_revisions",
        ),
        Index(
            "ix_query_experience_owner_role_last_used",
            "owner_user_id",
            "role_name",
            "last_used_at",
        ),
    )

    def refresh_from_success(
        self,
        *,
        purpose: str,
        representative_sql: str,
        sql_template: str,
        used_at: datetime,
    ) -> None:
        """记录成功复用或重建经验"""
        if purpose not in self.purposes:
            self.purposes = [*self.purposes, purpose]
        self.representative_sql = representative_sql
        self.sql_template = sql_template
        self.success_count += 1
        self.revision += 1
        self.last_used_at = used_at
        self.invalidated_at = None
        if self.quality == "disabled":
            self.quality = "candidate"


class QueryExperienceAsset(MetaBase):
    """查询经验关联的表或字段元数据快照"""

    __tablename__ = "query_experience_assets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    experience_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_experiences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(64), nullable=False)
    data_source: Mapped[str] = mapped_column(String(256), nullable=False)
    database_name: Mapped[str] = mapped_column(String(256), nullable=False)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    column_name: Mapped[str | None] = mapped_column(String(256))
    meta_version: Mapped[int] = mapped_column(Integer, nullable=False)
    experience: Mapped[QueryExperience] = relationship(back_populates="assets")

    __table_args__ = (
        UniqueConstraint(
            "experience_id",
            "resource_key",
            name="uq_query_experience_asset_resource",
        ),
        CheckConstraint(
            "(kind = 'table' AND column_name IS NULL) OR "
            "(kind = 'column' AND column_name IS NOT NULL)",
            name="ck_query_experience_asset_kind",
        ),
        Index(
            "ix_query_experience_asset_lookup",
            "kind",
            "table_name",
            "column_name",
        ),
    )


class QueryExecution(MetaBase):
    """一次 SQL 尝试及其最终采用状态"""

    __tablename__ = "query_executions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    experience_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("query_experiences.id", ondelete="SET NULL"),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    analysis_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(256))
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sql: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sql: Mapped[str | None] = mapped_column(Text)
    sql_template: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    dialect: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(Text)
    validation: Mapped[dict[str, object] | None] = mapped_column(JSON)
    plan_estimate: Mapped[dict[str, object] | None] = mapped_column(JSON)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSON)
    artifact_path: Mapped[str | None] = mapped_column(String(1024), index=True)
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class QueryAssetSnapshot(BaseModel):
    """查询经验返回的资产引用"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QueryAssetKind
    database: str
    table: str
    column: str | None = None
    meta_version: int


class QueryExperienceSearchResult(BaseModel):
    """提供给 Explorer 的紧凑查询经验"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experience_id: UUID
    purpose: str
    sql_template: str
    dialect: str
    assets: list[QueryAssetSnapshot]
    quality: QueryExperienceQuality
    success_count: int
    adopted_count: int
    score: float
    match_reasons: list[str]
    last_used_at: datetime
