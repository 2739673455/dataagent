"""元数据实体"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypedDict

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.entities.base import Base


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


class TableInfo(Base):
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


class ColumnInfo(Base):
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


class MetricInfo(Base):
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


class ColumnMetric(Base):
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
