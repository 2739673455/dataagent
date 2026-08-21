"""元数据模型"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import MetaBase

type QueryExecutionStatus = Literal["rejected", "failed", "succeeded"]
type QueryExperienceQuality = Literal["candidate", "promoted", "disabled"]
type QueryAssetKind = Literal["table", "column"]


class ColumnReference(TypedDict):
    """字段联合主键引用"""

    t_name: str
    c_name: str


type ColumnKey = tuple[str, str]

COLUMN_EXAMPLE_LIMIT = 10


def column_resource_key(t_name: str, c_name: str) -> str:
    """生成无歧义的表字段联合资源键"""
    return json.dumps(
        [t_name, c_name],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def serialize_column_examples(examples: list[Any]) -> list[Any]:
    """将字段示例转换为可序列化值"""
    serialized: list[Any] = []
    for value in examples:
        if isinstance(value, (datetime, date)):
            serialized.append(value.isoformat())
        elif isinstance(value, Decimal):
            serialized.append(float(value))
        else:
            serialized.append(value)
    return sorted(serialized, key=lambda value: str(value))


def _version_column(default: int, comment: str) -> Mapped[int]:
    """创建版本字段"""
    return mapped_column(
        Integer,
        nullable=False,
        default=default,
        server_default=text(str(default)),
        comment=comment,
    )


class TableInfo(MetaBase):
    """表信息"""

    __tablename__ = "table_info"

    name: Mapped[str] = mapped_column(String(256), primary_key=True, comment="表名称")
    role: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="表类型(fact/dim)"
    )
    primary_key_columns: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, comment="主键字段"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="表描述")
    meta_version: Mapped[int] = _version_column(1, "元数据版本")

    def metadata_snapshot(self) -> tuple[Any, ...]:
        """生成元数据内容快照"""
        return self.role, self.primary_key_columns, self.description


class ColumnInfo(MetaBase):
    """字段信息"""

    __tablename__ = "column_info"

    __table_args__ = (
        ForeignKeyConstraint(
            ["reference_t_name", "reference_c_name"],
            ["column_info.t_name", "column_info.name"],
            ondelete="SET NULL",
        ),
    )

    t_name: Mapped[str] = mapped_column(
        String(256),
        ForeignKey("table_info.name", ondelete="CASCADE"),
        primary_key=True,
        comment="所属表名称",
    )
    name: Mapped[str] = mapped_column(String(256), primary_key=True, comment="字段名称")
    type: Mapped[str] = mapped_column(String(256), nullable=False, comment="数据类型")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="列描述")
    examples: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, comment="数据示例"
    )
    alias: Mapped[list[str]] = mapped_column(JSON, nullable=False, comment="列别名")
    index_values: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否索引字段值",
    )
    reference_t_name: Mapped[str | None] = mapped_column(
        String(256), comment="引用表名称"
    )
    reference_c_name: Mapped[str | None] = mapped_column(
        String(256), comment="引用字段名称"
    )
    meta_version: Mapped[int] = _version_column(1, "元数据版本")
    index_version: Mapped[int] = _version_column(0, "语义索引版本")
    value_index_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="字段值索引最近同步成功时间",
    )
    value_index_sync_status: Mapped[str | None] = mapped_column(
        String(16),
        comment="字段值索引最近同步状态",
    )

    def metadata_snapshot(self) -> tuple[Any, ...]:
        """生成元数据内容快照"""
        return (
            self.type,
            self.description,
            self.examples,
            self.alias,
            self.index_values,
            self.reference_t_name,
            self.reference_c_name,
        )


@dataclass
class ValueInfo:
    """字段取值信息"""

    value: str
    t_name: str
    c_name: str


class MetricInfo(MetaBase):
    """指标信息"""

    __tablename__ = "metric_info"
    __allow_unmapped__ = True

    name: Mapped[str] = mapped_column(String(256), primary_key=True, comment="指标名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="指标描述")
    alias: Mapped[list[str]] = mapped_column(JSON, nullable=False, comment="指标别名")
    meta_version: Mapped[int] = _version_column(1, "元数据版本")
    index_version: Mapped[int] = _version_column(0, "语义索引版本")
    relevant_columns: list[ColumnReference]

    def __init__(
        self,
        *,
        name: str,
        description: str,
        alias: list[str],
        relevant_columns: list[ColumnReference] | None = None,
        meta_version: int = 1,
        index_version: int = 0,
    ) -> None:
        self.name = name
        self.description = description
        self.alias = alias
        self.relevant_columns = relevant_columns or []
        self.meta_version = meta_version
        self.index_version = index_version

    def metadata_snapshot(self) -> tuple[Any, ...]:
        """生成元数据内容快照"""
        return (
            self.description,
            tuple(
                sorted(
                    (reference["t_name"], reference["c_name"])
                    for reference in self.relevant_columns
                )
            ),
            self.alias,
        )


class ColumnMetric(MetaBase):
    """字段与指标关联"""

    __tablename__ = "column_metric"

    __table_args__ = (
        ForeignKeyConstraint(
            ["t_name", "c_name"],
            ["column_info.t_name", "column_info.name"],
            ondelete="CASCADE",
        ),
    )

    t_name: Mapped[str] = mapped_column(
        String(256),
        primary_key=True,
        comment="表名称",
    )
    c_name: Mapped[str] = mapped_column(
        String(256),
        primary_key=True,
        comment="字段名称",
    )
    metric_name: Mapped[str] = mapped_column(
        String(256),
        ForeignKey("metric_info.name", ondelete="CASCADE"),
        primary_key=True,
        comment="指标名称",
    )


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
