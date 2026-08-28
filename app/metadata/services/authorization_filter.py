"""元数据白名单过滤与引用脱敏"""

from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.catalog import ColumnInfo, ColumnKey, MetricInfo, TableInfo
from app.metadata.models.search import (
    SemanticColumnRecallResult,
    SemanticMetricRecallResult,
    SemanticResourceRecallResponse,
    SemanticValueRecallResult,
)


class MetadataAuthorizationFilter:
    """将元数据限制为用户可见的资产快照"""

    def __init__(
        self,
        policy: AssetAccessPolicy,
        data_source: str,
        database_name: str,
    ) -> None:
        """绑定当前用户资产策略和元数据数据库范围"""
        self._policy = policy
        self._data_source = data_source
        self._database_name = database_name

    def identity(
        self,
        table_name: str | None = None,
        column_name: str | None = None,
    ) -> AssetIdentity:
        """构造当前数据库内的资产标识"""
        return AssetIdentity(
            data_source=self._data_source,
            database_name=self._database_name,
            table_name=table_name,
            column_name=column_name,
        )

    def table_is_visible(self, table_name: str) -> bool:
        """判断表或任一下级字段是否对用户可见"""
        return self._policy.is_visible(self.identity(table_name))

    def column_is_allowed(self, table_name: str, column_name: str) -> bool:
        """判断字段是否具备完整读取权限"""
        return self._policy.allows(self.identity(table_name, column_name))

    def allowed_column_keys(
        self,
        column_infos: list[ColumnInfo],
    ) -> frozenset[ColumnKey]:
        """返回可以完整读取的字段键"""
        return frozenset(
            (item.t_name, item.name)
            for item in column_infos
            if self._policy.allows(self.identity(item.t_name, item.name))
        )

    def filter_tables(
        self,
        table_infos: list[TableInfo],
        allowed_columns: frozenset[ColumnKey],
    ) -> list[TableInfo]:
        """过滤表并移除未授权的主键名称"""
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
            if self.table_is_visible(item.name)
        ]

    def filter_columns(
        self,
        column_infos: list[ColumnInfo],
        allowed_columns: frozenset[ColumnKey],
    ) -> list[ColumnInfo]:
        """过滤字段并移除指向未授权资产的外键引用"""
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
        """仅保留依赖字段全部授权的指标"""
        database_allowed = self._policy.allows(self.identity())
        return [
            item
            for item in metric_infos
            if (
                {
                    (reference["t_name"], reference["c_name"])
                    for reference in item.relevant_columns
                }.issubset(allowed_columns)
                if item.relevant_columns
                else database_allowed
            )
        ]

    def filter_semantic_response(
        self,
        response: SemanticResourceRecallResponse,
    ) -> SemanticResourceRecallResponse:
        """按当前权限过滤已持久化的语义召回快照"""
        columns = []
        for item in response.columns:
            if not self.column_is_allowed(item.t_name, item.name):
                continue
            reference_allowed = (
                item.reference_t_name is not None
                and item.reference_c_name is not None
                and self.column_is_allowed(
                    item.reference_t_name,
                    item.reference_c_name,
                )
            )
            columns.append(
                item.model_copy(
                    update={
                        "reference_t_name": (
                            item.reference_t_name if reference_allowed else None
                        ),
                        "reference_c_name": (
                            item.reference_c_name if reference_allowed else None
                        ),
                    }
                )
            )
        metrics = [
            item
            for item in response.metrics
            if self._semantic_metric_is_allowed(item.relevant_columns)
        ]
        values = [
            item
            for item in response.values
            if self.column_is_allowed(item.t_name, item.c_name)
        ]
        tables = [
            item.model_copy(
                update={
                    "primary_key_columns": [
                        column_name
                        for column_name in item.primary_key_columns
                        if self.column_is_allowed(item.name, column_name)
                    ]
                }
            )
            for item in response.tables
            if self.table_is_visible(item.name)
        ]
        return response.model_copy(
            update={
                "metrics": metrics,
                "columns": columns,
                "values": values,
                "tables": tables,
                "warnings": self._filter_semantic_warnings(
                    response,
                    columns,
                    metrics,
                    values,
                ),
            }
        )

    @staticmethod
    def _filter_semantic_warnings(
        response: SemanticResourceRecallResponse,
        columns: list[SemanticColumnRecallResult],
        metrics: list[SemanticMetricRecallResult],
        values: list[SemanticValueRecallResult],
    ) -> list[str]:
        """移除指向已过滤资产的索引状态告警，保留通用告警"""
        allowed_column_keys = {(item.t_name, item.name) for item in columns}
        allowed_metric_names = {item.name for item in metrics}
        allowed_value_column_keys = {(item.t_name, item.c_name) for item in values}
        denied_warnings: set[str] = set()

        for item in response.columns:
            if (item.t_name, item.name) not in allowed_column_keys:
                denied_warnings.add(
                    "字段语义索引状态为 "
                    f"{item.index_status}: {item.t_name}.{item.name}"
                )
        for item in response.metrics:
            if item.name not in allowed_metric_names:
                denied_warnings.add(f"指标语义索引状态为 {item.index_status}: {item.name}")
        for item in response.values:
            if (item.t_name, item.c_name) not in allowed_value_column_keys:
                denied_warnings.add(
                    "字段取值索引状态为 "
                    f"{item.sync_status or '未知'}: {item.t_name}.{item.c_name}"
                )
        return [warning for warning in response.warnings if warning not in denied_warnings]

    def _semantic_metric_is_allowed(
        self,
        relevant_columns: list[dict[str, str]],
    ) -> bool:
        """判断召回指标的全部依赖字段是否仍获授权"""
        if not relevant_columns:
            return self._policy.allows(self.identity())
        return all(
            isinstance(reference.get("t_name"), str)
            and isinstance(reference.get("c_name"), str)
            and self.column_is_allowed(
                reference["t_name"],
                reference["c_name"],
            )
            for reference in relevant_columns
        )
