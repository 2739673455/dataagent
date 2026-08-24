"""元数据索引任务提交器"""

from typing import Any

from app.metadata.models import ColumnKey
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.submission import TaskSubmission


class CeleryMetadataSemanticIndexScheduler:
    """通过 Celery 提交元数据语义索引同步任务"""

    @staticmethod
    def _submit(name: str, args: list[Any]) -> TaskSubmission:
        """向元数据索引队列提交任务"""
        task = celery_app.send_task(
            name,
            args=args,
            queue="metadata-index",
            routing_key="metadata-index",
        )
        return TaskSubmission(task_id=task.id)

    def enqueue_columns(self, column_keys: list[ColumnKey]) -> TaskSubmission:
        """提交字段语义索引同步任务"""
        return self._submit(
            "dataagent.metadata.sync_column_indexes",
            [column_keys],
        )

    def enqueue_metrics(self, metric_names: list[str]) -> TaskSubmission:
        """提交指标语义索引同步任务"""
        return self._submit(
            "dataagent.metadata.sync_metric_indexes",
            [metric_names],
        )
