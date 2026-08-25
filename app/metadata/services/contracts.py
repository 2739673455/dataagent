"""元数据应用服务依赖的外部能力端口"""

from typing import Protocol

from app.metadata.models import ColumnKey
from app.shared.tasks.submission import TaskSubmission


class MetadataAssetInvalidator(Protocol):
    """使引用已变更元数据的派生资产失效"""

    async def invalidate_assets(
        self,
        *,
        table_names: set[str],
        column_keys: set[ColumnKey],
    ) -> object:
        """使引用指定表或字段的派生资产失效"""
        ...


class MetadataSemanticIndexScheduler(Protocol):
    """提交元数据语义索引同步任务"""

    def enqueue_columns(self, column_keys: list[ColumnKey]) -> TaskSubmission:
        """提交字段语义索引同步任务"""
        ...

    def enqueue_metrics(self, metric_names: list[str]) -> TaskSubmission:
        """提交指标语义索引同步任务"""
        ...
