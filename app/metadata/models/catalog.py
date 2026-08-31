"""元数据目录模型"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import MetaBase


class ColumnReference(TypedDict):
    """字段联合主键引用"""

    t_name: str
    c_name: str


type ColumnKey = tuple[str, str]
type ValueIndexSyncStatus = Literal["syncing", "succeeded", "failed"]

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
    return sorted(serialized, key=str)


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
    value_index_cursor_column: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="字段取值索引增量游标字段",
    )
    meta_version: Mapped[int] = _version_column(1, "元数据版本")

    def metadata_snapshot(self) -> tuple[Any, ...]:
        """生成元数据内容快照"""
        return (
            self.role,
            self.primary_key_columns,
            self.description,
            self.value_index_cursor_column,
        )


class ColumnInfo(MetaBase):
    """字段信息"""

    __tablename__ = "column_info"
    # Repository 在查询后批量填充索引状态；保持非 ORM relationship，避免序列化
    # 阶段触发 AsyncSession 无法安全执行的隐式懒加载。
    __allow_unmapped__ = True

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
    value_index_state: "ValueIndexSyncState | None" = None

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


class ValueIndexSyncState(MetaBase):
    """字段取值索引增量同步状态"""

    __tablename__ = "value_index_sync_state"

    __table_args__ = (
        ForeignKeyConstraint(
            ["t_name", "c_name"],
            ["column_info.t_name", "column_info.name"],
            ondelete="CASCADE",
        ),
    )

    t_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    c_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    cursor_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    active_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    current_generation: Mapped[UUID | None] = mapped_column(Uuid)
    active_generation: Mapped[UUID | None] = mapped_column(Uuid)
    last_incremental_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_full_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    @property
    def last_synced_at(self) -> datetime | None:
        """返回最近一次成功的增量或全量同步时间"""
        timestamps = [
            timestamp
            for timestamp in (
                self.last_incremental_synced_at,
                self.last_full_synced_at,
            )
            if timestamp is not None
        ]
        return max(timestamps, default=None)

    @property
    def last_sync_mode(self) -> Literal["full", "incremental"] | None:
        """返回最近一次成功同步的模式"""
        if self.last_full_synced_at is None:
            return "incremental" if self.last_incremental_synced_at else None
        if self.last_incremental_synced_at is None:
            return "full"
        if self.last_full_synced_at >= self.last_incremental_synced_at:
            return "full"
        return "incremental"


@dataclass
class ValueInfo:
    """字段取值信息"""

    value: str
    t_name: str
    c_name: str


class MetricInfo(MetaBase):
    """指标信息"""

    __tablename__ = "metric_info"
    # 相关字段由 Repository 批量投影，原因同 ColumnInfo.value_index_state。
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
        """初始化指标元数据及其关联字段引用"""
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
