"""元数据导入导出配置模型"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

TableRole = Literal["fact", "dim"]
MetadataName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
MetadataDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
MetadataAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class MetaConfigModel(BaseModel):
    """元数据配置模型基类"""

    model_config = ConfigDict(extra="forbid")


class ColumnConfig(MetaConfigModel):
    """字段元数据配置"""

    name: MetadataName
    description: MetadataDescription
    alias: list[MetadataAlias] = Field(default_factory=list, max_length=100)
    index_values: bool
    reference_t_name: MetadataName | None = None
    reference_c_name: MetadataName | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> "ColumnConfig":
        """校验字段引用必须同时包含表名和字段名"""
        if (self.reference_t_name is None) != (self.reference_c_name is None):
            raise ValueError("引用表名和引用字段名必须同时提供")
        return self


class TableConfig(MetaConfigModel):
    """表元数据配置"""

    name: MetadataName
    role: TableRole
    description: MetadataDescription
    columns: list[ColumnConfig] = Field(default_factory=list)


class ColumnReferenceConfig(MetaConfigModel):
    """字段联合主键引用"""

    t_name: MetadataName
    c_name: MetadataName


class MetricConfig(MetaConfigModel):
    """指标元数据配置"""

    name: MetadataName
    description: MetadataDescription
    relevant_columns: list[ColumnReferenceConfig] = Field(
        default_factory=list,
        max_length=100,
    )
    alias: list[MetadataAlias] = Field(default_factory=list, max_length=100)


class MetaConfig(MetaConfigModel):
    """元数据导入导出配置"""

    tables: list[TableConfig] = Field(default_factory=list)
    metrics: list[MetricConfig] = Field(default_factory=list)
