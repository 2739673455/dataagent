"""元数据变更后的跨领域编排：查询经验失效，再提交语义索引同步。"""

from app.metadata.models.changes import MetadataChanges, MetadataChangeTasks
from app.metadata.task_scheduler import CeleryMetadataSemanticIndexScheduler
from app.query.services.experience_invalidation import (
    QueryExperienceInvalidationService,
)


class MetadataChangeWorkflow:
    """处理已经提交的目录变更；不拥有目录事务，也不吞掉提交失败。"""

    def __init__(
        self,
        asset_invalidator: QueryExperienceInvalidationService,
        index_scheduler: CeleryMetadataSemanticIndexScheduler,
    ) -> None:
        """绑定查询经验失效能力和元数据索引调度能力。"""
        self._asset_invalidator = asset_invalidator
        self._index_scheduler = index_scheduler

    async def handle(self, changes: MetadataChanges) -> MetadataChangeTasks:
        """先失效旧经验；成功后依次投递字段、指标的新版本索引。"""
        if changes.invalidated_tables or changes.invalidated_columns:
            await self._asset_invalidator.invalidate_assets(
                table_names=set(changes.invalidated_tables),
                column_keys=set(changes.invalidated_columns),
            )
        columns = (
            self._index_scheduler.enqueue_columns(list(changes.sync_columns))
            if changes.sync_columns
            else None
        )
        metrics = (
            self._index_scheduler.enqueue_metrics(list(changes.sync_metrics))
            if changes.sync_metrics
            else None
        )
        return MetadataChangeTasks(columns=columns, metrics=metrics)
