"""元数据 Celery 任务提交协议。"""

from typing import Any

from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.submission import TaskSubmission

METADATA_TASK_QUEUE = "metadata-index"
SYNC_TABLE_INDEXES_TASK = "dataagent.metadata.sync_table_indexes"
SYNC_TABLE_VALUES_TASK = "dataagent.metadata.sync_table_values"
SYNC_COLUMN_INDEXES_TASK = "dataagent.metadata.sync_column_indexes"
SYNC_COLUMN_VALUES_TASK = "dataagent.metadata.sync_column_values"
SYNC_METRIC_INDEXES_TASK = "dataagent.metadata.sync_metric_indexes"
IMPORT_METADATA_TASK = "dataagent.metadata.import"
DISPATCH_VALUE_INDEXES_TASK = "dataagent.metadata.dispatch_value_indexes"


def submit_metadata_task(name: str, args: list[Any]) -> TaskSubmission:
    """向元数据索引队列提交任务。"""
    task = celery_app.send_task(
        name,
        args=args,
        queue=METADATA_TASK_QUEUE,
        routing_key=METADATA_TASK_QUEUE,
    )
    return TaskSubmission(task_id=task.id)
