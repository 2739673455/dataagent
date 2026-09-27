"""元数据目录模型。"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypedDict

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import MetaBase


class ColumnReference(TypedDict):
    """字段联合主键引用。"""

    t_name: str
    c_name: str


type ColumnKey = tuple[str, str]

COLUMN_EXAMPLE_LIMIT = 10


def column_reference_key(reference: ColumnReference) -> ColumnKey:
    """将字段引用转换为可用于集合和映射的联合键。"""
    return reference["t_name"], reference["c_name"]


def column_key_reference(key: ColumnKey) -> ColumnReference:
    """将字段联合键转换为 JSON 可序列化的字段引用。"""
    return ColumnReference(t_name=key[0], c_name=key[1])


def column_resource_key(t_name: str, c_name: str) -> str:
    """生成无歧义的表字段联合资源键。"""
    return json.dumps(
        [t_name, c_name],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def serialize_column_examples(examples: list[Any]) -> list[Any]:
    """转换日期、时间和小数样例，并按字符串形式稳定排序。"""
    serialized: list[Any] = []
    for value in examples:
        if isinstance(value, (datetime, date)):
            serialized.append(value.isoformat())
        elif isinstance(value, Decimal):
            serialized.append(float(value))
        else:
            serialized.append(value)
    return sorted(serialized, key=str)


class TableInfo(MetaBase):
    """表信息。"""

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


class ColumnInfo(MetaBase):
    """字段信息。"""

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
    value_index_cursor_value: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="字段取值索引已提交水位"
    )


class MetricInfo(MetaBase):
    """指标信息。"""

    __tablename__ = "metric_info"
    # 相关字段由 Repository 批量填充，避免异步序列化时触发隐式懒加载。
    __allow_unmapped__ = True

    name: Mapped[str] = mapped_column(String(256), primary_key=True, comment="指标名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="指标描述")
    alias: Mapped[list[str]] = mapped_column(JSON, nullable=False, comment="指标别名")
    relevant_columns: list[ColumnReference]

    def __init__(
        self,
        *,
        name: str,
        description: str,
        alias: list[str],
        relevant_columns: list[ColumnReference] | None = None,
    ) -> None:
        """初始化指标元数据及其关联字段引用。"""
        self.name = name
        self.description = description
        self.alias = alias
        self.relevant_columns = relevant_columns or []


class ColumnMetric(MetaBase):
    """字段与指标关联。"""

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


@dataclass
class ValueInfo:
    """字段取值信息。"""

    value: str
    t_name: str
    c_name: str
