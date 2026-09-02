"""元数据索引任务提交器。"""

from loguru import logger

from app.metadata.models.catalog import ColumnKey
from app.metadata.task_submission import (
    SYNC_COLUMN_INDEXES_TASK,
    SYNC_METRIC_INDEXES_TASK,
    submit_metadata_task,
)
from app.shared.tasks.submission import TaskSubmission


class CeleryMetadataSemanticIndexScheduler:
    """通过 Celery 提交元数据语义索引同步任务。"""

    def enqueue_columns(self, column_keys: list[ColumnKey]) -> TaskSubmission:
        """提交字段语义索引同步任务。"""
        submission = submit_metadata_task(
            SYNC_COLUMN_INDEXES_TASK,
            [column_keys],
        )
        logger.info(
            "自动提交字段语义索引同步任务: "
            f"task_id={submission.task_id}, column_count={len(column_keys)}, "
            f"columns={column_keys[:20]}, truncated={len(column_keys) > 20}"
        )
        return submission

    def enqueue_metrics(self, metric_names: list[str]) -> TaskSubmission:
        """提交指标语义索引同步任务。"""
        submission = submit_metadata_task(
            SYNC_METRIC_INDEXES_TASK,
            [metric_names],
        )
        logger.info(
            "自动提交指标语义索引同步任务: "
            f"task_id={submission.task_id}, metric_count={len(metric_names)}, "
            f"metrics={metric_names[:20]}, truncated={len(metric_names) > 20}"
        )
        return submission
