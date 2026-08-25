"""元数据白名单过滤与引用脱敏"""

from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models import ColumnInfo, ColumnKey, MetricInfo, TableInfo
from app.metadata.search_models import SemanticSearchResponse


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

    @property
    def unrestricted(self) -> bool:
        """返回当前策略是否可查看完整目录"""
        return self._policy.unrestricted

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
        if self._policy.unrestricted:
            return frozenset((item.t_name, item.name) for item in column_infos)
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
        if self._policy.unrestricted:
            return table_infos
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
                value_index_sync=item.value_index_sync,
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
        if self._policy.unrestricted:
            return column_infos
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
        if self._policy.unrestricted:
            return metric_infos
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
        response: SemanticSearchResponse,
    ) -> SemanticSearchResponse:
        """按当前权限过滤已持久化的语义召回快照"""
        if self._policy.unrestricted:
            return response
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
        return response.model_copy(
            update={
                "metrics": [
                    item
                    for item in response.metrics
                    if self._semantic_metric_is_allowed(item.relevant_columns)
                ],
                "columns": columns,
                "values": [
                    item
                    for item in response.values
                    if self.column_is_allowed(item.t_name, item.c_name)
                ],
                "tables": [
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
                ],
                "relations": [
                    item
                    for item in response.relations
                    if self.column_is_allowed(
                        item.source_t_name,
                        item.source_c_name,
                    )
                    and self.column_is_allowed(
                        item.target_t_name,
                        item.target_c_name,
                    )
                ],
                "warnings": [],
            }
        )

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
