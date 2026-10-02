"""元数据白名单过滤与引用脱敏。"""

from app.metadata.application.authorization import SemanticRecallAuthorization
from app.metadata.contracts import (
    ColumnKey,
    column_reference_key,
)
from app.metadata.models.catalog import ColumnInfo, MetricInfo, TableInfo


class MetadataAuthorizationFilter(SemanticRecallAuthorization):
    """将元数据限制为用户可见的资产快照。"""

    def allowed_column_keys(
        self,
        column_infos: list[ColumnInfo],
    ) -> frozenset[ColumnKey]:
        """返回可以完整读取的字段键。"""
        return frozenset(
            (item.t_name, item.name)
            for item in column_infos
            if self.policy.allows(self._identity(item.t_name, item.name))
        )

    def filter_tables(
        self,
        table_infos: list[TableInfo],
        allowed_columns: frozenset[ColumnKey],
    ) -> list[TableInfo]:
        """过滤表并移除未授权的主键名称。"""
        return [
            TableInfo(
                name=item.name,
                role=item.role,
                primary_key_columns=[
                    name
                    for name in item.primary_key_columns
                    if (item.name, name) in allowed_columns
                ],
                description=item.description,
                value_index_cursor_column=item.value_index_cursor_column,
                meta_version=item.meta_version,
            )
            for item in table_infos
            if self.policy.is_visible(self._identity(item.name))
        ]

    def filter_columns(
        self,
        column_infos: list[ColumnInfo],
        allowed_columns: frozenset[ColumnKey],
    ) -> list[ColumnInfo]:
        """过滤字段并移除指向未授权资产的外键引用。"""
        filtered: list[ColumnInfo] = []
        for item in column_infos:
            if (item.t_name, item.name) not in allowed_columns:
                continue
            target_allowed = (
                item.reference_t_name is not None
                and item.reference_c_name is not None
                and (item.reference_t_name, item.reference_c_name) in allowed_columns
            )
            filtered_item = ColumnInfo(
                t_name=item.t_name,
                name=item.name,
                type=item.type,
                description=item.description,
                examples=item.examples,
                alias=item.alias,
                index_values=item.index_values,
                reference_t_name=item.reference_t_name if target_allowed else None,
                reference_c_name=item.reference_c_name if target_allowed else None,
                meta_version=item.meta_version,
                index_version=item.index_version,
            )
            filtered_item.value_index_state = item.value_index_state
            filtered.append(filtered_item)
        return filtered

    def filter_metrics(
        self,
        metric_infos: list[MetricInfo],
        allowed_columns: frozenset[ColumnKey],
    ) -> list[MetricInfo]:
        """仅保留依赖字段全部授权的指标。"""
        database_allowed = self.policy.allows(self._identity())
        return [
            item
            for item in metric_infos
            if (
                {
                    column_reference_key(reference)
                    for reference in item.relevant_columns
                }.issubset(allowed_columns)
                if item.relevant_columns
                else database_allowed
            )
        ]
