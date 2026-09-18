"""元数据任务名称与提交入口；手工同步和自动调度共用中央路由。"""

from typing import Any

from loguru import logger

from app.metadata.config import MetaConfig
from app.metadata.models.catalog import ColumnKey
from app.metadata.models.search import RequestedValueIndexSyncMode
from app.metadata.services.import_service import ImportMode
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.submission import TaskSubmission

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
    )
    submission = TaskSubmission(task_id=task.id)
    logger.info(f"元数据后台任务已提交: task_id={submission.task_id}, name={name}")
    return submission


def enqueue_table_indexes(table_names: list[str]) -> TaskSubmission:
    """提交多个表的字段语义索引同步任务。"""
    return submit_metadata_task(SYNC_TABLE_INDEXES_TASK, [table_names])


def enqueue_table_values(
    table_names: list[str],
    *,
    mode: RequestedValueIndexSyncMode,
) -> TaskSubmission:
    """提交多个表的字段取值索引同步任务。"""
    return submit_metadata_task(
        SYNC_TABLE_VALUES_TASK,
        [table_names, mode],
    )


def enqueue_column_indexes(column_keys: list[tuple[str, str]]) -> TaskSubmission:
    """提交指定字段的语义索引同步任务。"""
    return submit_metadata_task(SYNC_COLUMN_INDEXES_TASK, [column_keys])


def enqueue_column_values(
    column_keys: list[tuple[str, str]],
    *,
    mode: RequestedValueIndexSyncMode,
) -> TaskSubmission:
    """提交指定字段的取值索引同步任务。"""
    return submit_metadata_task(
        SYNC_COLUMN_VALUES_TASK,
        [column_keys, mode],
    )


def enqueue_metric_indexes(metric_names: list[str]) -> TaskSubmission:
    """提交指定指标的语义索引同步任务。"""
    return submit_metadata_task(SYNC_METRIC_INDEXES_TASK, [metric_names])


def enqueue_import(
    meta_config: MetaConfig,
    mode: ImportMode,
) -> TaskSubmission:
    """提交元数据配置导入任务。"""
    return submit_metadata_task(
        IMPORT_METADATA_TASK,
        [meta_config.model_dump(mode="json"), mode.value],
    )


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
