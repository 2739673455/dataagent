"""元数据变更后的语义索引同步。"""

from app.metadata.models.changes import MetadataChanges, MetadataChangeTasks
from app.metadata.task_scheduler import CeleryMetadataSemanticIndexScheduler


class MetadataChangeWorkflow:
    """处理已经提交的目录变更；不拥有目录事务，也不吞掉提交失败。"""

    def __init__(
        self,
        index_scheduler: CeleryMetadataSemanticIndexScheduler,
    ) -> None:
        """绑定元数据索引调度能力。"""
        self._index_scheduler = index_scheduler

    async def handle(self, changes: MetadataChanges) -> MetadataChangeTasks:
        """依次投递字段、指标的新版本索引。"""
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
