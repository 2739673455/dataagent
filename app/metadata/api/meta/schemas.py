"""元数据管理接口模型"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.config.meta_config import (
    MetadataAlias,
    MetadataDescription,
    MetadataName,
    TableRole,
)


class MetaRequestModel(BaseModel):
    """元数据请求模型基类"""

    model_config = ConfigDict(extra="forbid")


class TableInfoRequest(MetaRequestModel):
    """表元数据写入请求"""

    role: TableRole
    description: MetadataDescription


class ColumnInfoRequest(MetaRequestModel):
    """字段元数据写入请求"""

    description: MetadataDescription
    alias: list[MetadataAlias] = Field(default_factory=list, max_length=100)
    index_values: bool
    reference_t_name: MetadataName | None = None
    reference_c_name: MetadataName | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> "ColumnInfoRequest":
        """校验字段引用必须同时包含表名和字段名"""
        if (self.reference_t_name is None) != (self.reference_c_name is None):
            raise ValueError("引用表名和引用列名必须同时提供")
        return self


class ColumnReference(MetaRequestModel):
    """字段联合主键引用"""

    t_name: MetadataName
    c_name: MetadataName


class MetricInfoRequest(MetaRequestModel):
    """指标元数据写入请求"""

    description: MetadataDescription
    relevant_columns: list[ColumnReference] = Field(
        default_factory=list,
        max_length=100,
    )
    alias: list[MetadataAlias] = Field(default_factory=list, max_length=100)


class TableInfoResponse(BaseModel):
    """表元数据响应"""

    model_config = ConfigDict(from_attributes=True)

    name: str
    role: TableRole
    primary_key_columns: list[str]
    description: str
    meta_version: int


class ColumnInfoResponse(BaseModel):
    """字段元数据响应"""

    model_config = ConfigDict(from_attributes=True)

    t_name: str
    name: str
    type: str
    examples: list[Any]
    description: str
    alias: list[str]
    index_values: bool
    reference_t_name: str | None
    reference_c_name: str | None
    meta_version: int
    index_version: int
    value_index_synced_at: datetime | None
    value_index_sync_status: Literal["syncing", "succeeded", "failed"] | None


class MetricInfoResponse(BaseModel):
    """指标元数据响应"""

    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str
    relevant_columns: list[ColumnReference]
    alias: list[str]
    meta_version: int
    index_version: int


class TableIndexSyncRequest(MetaRequestModel):
    """批量表索引同步请求"""

    tables: list[MetadataName] = Field(min_length=1, max_length=10000)


class TableBatchDeleteRequest(MetaRequestModel):
    """批量删除表元数据请求"""

    tables: list[MetadataName] = Field(min_length=1, max_length=10000)


class ColumnIndexSyncRequest(MetaRequestModel):
    """批量字段索引同步请求"""

    columns: list[ColumnReference] = Field(min_length=1, max_length=10000)


class ColumnBatchDeleteRequest(MetaRequestModel):
    """批量删除字段元数据请求"""

    columns: list[ColumnReference] = Field(min_length=1, max_length=10000)


class MetricIndexSyncRequest(MetaRequestModel):
    """批量指标索引同步请求"""

    metrics: list[MetadataName] = Field(min_length=1, max_length=10000)


class MetricBatchDeleteRequest(MetaRequestModel):
    """批量删除指标元数据请求"""

    metrics: list[MetadataName] = Field(min_length=1, max_length=10000)


class ColumnIndexSyncResponse(BaseModel):
    """字段索引同步响应"""

    t_name: str
    c_name: str
    indexed_count: int


class MetricIndexSyncResponse(BaseModel):
    """指标索引同步响应"""

    metric_name: str
    indexed_count: int


class BatchIndexSyncResponse(BaseModel):
    """批量索引同步响应"""

    results: list[ColumnIndexSyncResponse]


class BatchMetricIndexSyncResponse(BaseModel):
    """批量指标索引同步响应"""

    results: list[MetricIndexSyncResponse]


class ResourceImportChanges(BaseModel):
    """单类元数据导入变更"""

    created_count: int
    updated_count: int
    deleted_count: int
    created_keys: list[str]
    updated_keys: list[str]
    deleted_keys: list[str]


class MetaImportResponse(BaseModel):
    """元数据导入响应"""

    mode: str
    dry_run: bool
    tables: ResourceImportChanges
    columns: ResourceImportChanges
    metrics: ResourceImportChanges
