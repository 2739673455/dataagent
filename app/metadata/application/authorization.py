"""对语义检索结果和会话快照进行权限投影，不依赖存储模型。"""

from app.identity.contracts import AssetAccessPolicy, AssetIdentity
from app.metadata.contracts import (
    ColumnReference,
    SemanticColumnRecallResult,
    SemanticMetricRecallResult,
    SemanticResourceRecallResponse,
    SemanticValueRecallResult,
)


class SemanticRecallAuthorization:
    """按本次授权快照过滤公开的语义资源结果。"""

    def __init__(
        self,
        policy: AssetAccessPolicy,
        data_source: str,
        database_name: str,
    ) -> None:
        """绑定当前用户资产策略和元数据数据库范围。"""
        self.policy = policy
        self.data_source = data_source
        self.database_name = database_name

    def column_is_allowed(self, table_name: str, column_name: str) -> bool:
        """判断字段是否具备完整读取权限。"""
        return self.policy.allows(self._identity(table_name, column_name))

    def filter_recall_response(
        self,
        response: SemanticResourceRecallResponse,
    ) -> SemanticResourceRecallResponse:
        """按当前权限过滤已持久化的语义召回快照。"""
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
            if self.policy.is_visible(self._identity(item.name))
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

    def _identity(
        self,
        table_name: str | None = None,
        column_name: str | None = None,
    ) -> AssetIdentity:
        """构造当前数据库内的资产标识。"""
        return AssetIdentity(
            data_source=self.data_source,
            database_name=self.database_name,
            table_name=table_name,
            column_name=column_name,
        )

    @staticmethod
    def _filter_semantic_warnings(
        response: SemanticResourceRecallResponse,
        columns: list[SemanticColumnRecallResult],
        metrics: list[SemanticMetricRecallResult],
        values: list[SemanticValueRecallResult],
    ) -> list[str]:
        """移除指向已过滤资产的索引状态告警，保留通用告警。"""
        allowed_column_keys = {(item.t_name, item.name) for item in columns}
        allowed_metric_names = {item.name for item in metrics}
        allowed_value_column_keys = {(item.t_name, item.c_name) for item in values}
        denied_warnings: set[str] = set()

        for item in response.columns:
            if (item.t_name, item.name) not in allowed_column_keys:
                denied_warnings.add(
                    f"字段语义索引状态为 {item.index_status}: {item.t_name}.{item.name}"
                )
        for item in response.metrics:
            if item.name not in allowed_metric_names:
                denied_warnings.add(
                    f"指标语义索引状态为 {item.index_status}: {item.name}"
                )
        for item in response.values:
            if (item.t_name, item.c_name) not in allowed_value_column_keys:
                denied_warnings.add(
                    "字段取值索引状态为 "
                    f"{item.sync_status or '未知'}: {item.t_name}.{item.c_name}"
                )
        return [
            warning for warning in response.warnings if warning not in denied_warnings
        ]

    def _semantic_metric_is_allowed(
        self,
        relevant_columns: list[ColumnReference],
    ) -> bool:
        """判断召回指标的全部依赖字段是否仍获授权。"""
        if not relevant_columns:
            return self.policy.allows(self._identity())
        return all(
            isinstance(reference.get("t_name"), str)
            and isinstance(reference.get("c_name"), str)
            and self.column_is_allowed(
                reference["t_name"],
                reference["c_name"],
            )
            for reference in relevant_columns
        )
