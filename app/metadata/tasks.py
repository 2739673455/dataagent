"""元数据导入与索引同步后台任务"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from app.metadata.config import MetaConfig
from app.metadata.models.search import (
    RequestedValueIndexSyncMode,
    SemanticIndexSyncResult,
    ValueIndexSyncResult,
)
from app.metadata.providers import (
    build_meta_import_service,
    build_meta_index_service,
)
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.import_service import ImportMode, MetaImportResult
from app.shared.clients.doris_client_manager import admin_doris_client_manager
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import meta_postgres_client_manager
from app.shared.config.app_config import cfg
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.runner import run_async
from app.shared.tasks.submission import TaskSubmission

_PERIODIC_BATCH_SIZE = 50


async def _run_with_metadata_resources[T](
    operation: Callable[[MetaPGRepo, SourceDorisRepo], Awaitable[T]],
) -> T:
    """初始化元数据任务资源并执行指定异步操作"""
    embedding_client_manager.init()
    es_client_manager.init()
    meta_postgres_client_manager.init()
    admin_doris_client_manager.init()
    try:
        async with (
            meta_postgres_client_manager.session() as session,
            admin_doris_client_manager.connection() as connection,
        ):
            return await operation(
                MetaPGRepo(session),
                SourceDorisRepo(connection),
            )
    finally:
        await admin_doris_client_manager.close()
        await meta_postgres_client_manager.close()
        await es_client_manager.close()
        await embedding_client_manager.close()


def _column_semantic_results(
    results: dict[tuple[str, str], SemanticIndexSyncResult],
) -> list[dict[str, Any]]:
    """将字段语义索引同步结果转换为任务响应结构"""
    return [
        {"t_name": t_name, "c_name": c_name, **asdict(result)}
        for (t_name, c_name), result in results.items()
    ]


def _column_value_results(
    results: dict[tuple[str, str], ValueIndexSyncResult],
) -> list[dict[str, Any]]:
    """将字段取值索引同步结果转换为任务响应结构"""
    return [
        {"t_name": t_name, "c_name": c_name, **asdict(result)}
        for (t_name, c_name), result in results.items()
    ]


def _metric_semantic_results(
    results: dict[str, SemanticIndexSyncResult],
) -> list[dict[str, Any]]:
    """将指标语义索引同步结果转换为任务响应结构"""
    return [
        {"metric_name": metric_name, **asdict(result)}
        for metric_name, result in results.items()
    ]


def _format_key(key: str | tuple[str, str]) -> str:
    """将元数据资源键格式化为可序列化文本"""
    return ".".join(key) if isinstance(key, tuple) else key


def _import_result(result: MetaImportResult) -> dict[str, Any]:
    """汇总元数据导入结果中的各类资源变更"""

    def changes(value: Any) -> dict[str, Any]:
        """统计单类资源的新增、更新和删除明细"""
        return {
            "created_count": len(value.created),
            "updated_count": len(value.updated),
            "deleted_count": len(value.deleted),
            "created_keys": [_format_key(key) for key in value.created],
            "updated_keys": [_format_key(key) for key in value.updated],
            "deleted_keys": [_format_key(key) for key in value.deleted],
        }

    return {
        "mode": result.mode.value,
        "dry_run": result.dry_run,
        "tables": changes(result.tables),
        "columns": changes(result.columns),
        "metrics": changes(result.metrics),
    }


def _submit(name: str, args: list[Any]) -> TaskSubmission:
    """向元数据索引队列提交指定 Celery 任务"""
    task = celery_app.send_task(
        name,
        args=args,
        queue="metadata-index",
        routing_key="metadata-index",
    )
    submission = TaskSubmission(task_id=task.id)
    logger.info(f"元数据后台任务已提交: task_id={submission.task_id}, name={name}")
    return submission


def enqueue_table_indexes(table_names: list[str]) -> TaskSubmission:
    """提交多个表的字段语义索引同步任务"""
    return _submit("dataagent.metadata.sync_table_indexes", [table_names])


def enqueue_table_values(
    table_names: list[str],
    *,
    mode: RequestedValueIndexSyncMode,
) -> TaskSubmission:
    """提交多个表的字段取值索引同步任务"""
    return _submit(
        "dataagent.metadata.sync_table_values",
        [table_names, mode],
    )


def enqueue_column_indexes(column_keys: list[tuple[str, str]]) -> TaskSubmission:
    """提交指定字段的语义索引同步任务"""
    return _submit("dataagent.metadata.sync_column_indexes", [column_keys])


def enqueue_column_values(
    column_keys: list[tuple[str, str]],
    *,
    mode: RequestedValueIndexSyncMode,
) -> TaskSubmission:
    """提交指定字段的取值索引同步任务"""
    return _submit(
        "dataagent.metadata.sync_column_values",
        [column_keys, mode],
    )


def enqueue_metric_indexes(metric_names: list[str]) -> TaskSubmission:
    """提交指定指标的语义索引同步任务"""
    return _submit("dataagent.metadata.sync_metric_indexes", [metric_names])


def enqueue_import(
    meta_config: MetaConfig,
    mode: ImportMode,
) -> TaskSubmission:
    """提交元数据配置导入任务"""
    return _submit(
        "dataagent.metadata.import",
        [meta_config.model_dump(mode="json"), mode.value],
    )


@celery_app.task(
    name="dataagent.metadata.sync_table_indexes",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_table_indexes_task(table_names: list[str]) -> dict[str, Any]:
    """执行多个表的字段语义索引同步"""
    logger.info(
        "开始执行表字段语义索引同步任务: "
        f"table_count={len(table_names)}, tables={table_names[:20]}, "
        f"truncated={len(table_names) > 20}"
    )

    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        """使用任务级仓储执行表字段语义索引同步"""
        return await build_meta_index_service(
            meta_repo, source_repo
        ).sync_table_indexes(table_names)

    results = _column_semantic_results(
        run_async(_run_with_metadata_resources(operation))
    )
    logger.info(
        "表字段语义索引同步任务完成: "
        f"table_count={len(table_names)}, result_count={len(results)}"
    )
    return {"results": results}


@celery_app.task(
    name="dataagent.metadata.sync_table_values",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_table_values_task(
    table_names: list[str],
    mode: RequestedValueIndexSyncMode,
) -> dict[str, Any]:
    """执行多个表的字段取值索引同步"""
    logger.info(
        "开始执行表字段取值索引同步任务: "
        f"table_count={len(table_names)}, mode={mode}, "
        f"tables={table_names[:20]}, truncated={len(table_names) > 20}"
    )

    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        """使用任务级仓储执行表字段取值索引同步"""
        return await build_meta_index_service(meta_repo, source_repo).sync_table_values(
            table_names,
            mode=mode,
        )

    results = _column_value_results(run_async(_run_with_metadata_resources(operation)))
    logger.info(
        "表字段取值索引同步任务完成: "
        f"table_count={len(table_names)}, result_count={len(results)}, "
        f"mode={mode}"
    )
    return {"results": results}


@celery_app.task(
    name="dataagent.metadata.sync_column_indexes",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_column_indexes_task(column_keys: list[list[str]]) -> dict[str, Any]:
    """执行指定字段的语义索引同步"""
    keys = [(t_name, c_name) for t_name, c_name in column_keys]
    logger.info(
        "开始执行字段语义索引同步任务: "
        f"column_count={len(keys)}, columns={keys[:20]}, "
        f"truncated={len(keys) > 20}"
    )

    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        """使用任务级仓储执行字段语义索引同步"""
        return await build_meta_index_service(
            meta_repo, source_repo
        ).sync_column_indexes(keys)

    results = _column_semantic_results(
        run_async(_run_with_metadata_resources(operation))
    )
    logger.info(
        "字段语义索引同步任务完成: "
        f"column_count={len(keys)}, result_count={len(results)}"
    )
    return {"results": results}


@celery_app.task(
    name="dataagent.metadata.sync_column_values",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_column_values_task(
    column_keys: list[list[str]],
    mode: RequestedValueIndexSyncMode,
) -> dict[str, Any]:
    """执行指定字段的取值索引同步"""
    keys = [(t_name, c_name) for t_name, c_name in column_keys]
    logger.info(
        "开始执行字段取值索引同步任务: "
        f"column_count={len(keys)}, mode={mode}, "
        f"columns={keys[:20]}, truncated={len(keys) > 20}"
    )

    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        """使用任务级仓储执行字段取值索引同步"""
        return await build_meta_index_service(
            meta_repo, source_repo
        ).sync_column_values(
            keys,
            mode=mode,
        )

    results = _column_value_results(run_async(_run_with_metadata_resources(operation)))
    logger.info(
        "字段取值索引同步任务完成: "
        f"column_count={len(keys)}, result_count={len(results)}, "
        f"mode={mode}"
    )
    return {"results": results}


@celery_app.task(
    name="dataagent.metadata.sync_metric_indexes",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_metric_indexes_task(metric_names: list[str]) -> dict[str, Any]:
    """执行指定指标的语义索引同步"""
    logger.info(
        "开始执行指标语义索引同步任务: "
        f"metric_count={len(metric_names)}, metrics={metric_names[:20]}, "
        f"truncated={len(metric_names) > 20}"
    )

    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        """使用任务级仓储执行指标语义索引同步"""
        return await build_meta_index_service(
            meta_repo, source_repo
        ).sync_metric_indexes(metric_names)

    results = _metric_semantic_results(
        run_async(_run_with_metadata_resources(operation))
    )
    logger.info(
        "指标语义索引同步任务完成: "
        f"metric_count={len(metric_names)}, result_count={len(results)}"
    )
    return {"results": results}


@celery_app.task(
    name="dataagent.metadata.import",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def import_metadata_task(payload: dict[str, Any], mode: str) -> dict[str, Any]:
    """执行元数据配置导入并返回变更摘要"""
    logger.info(
        "开始执行元数据导入任务: "
        f"mode={mode}, table_count={len(payload.get('tables', []))}, "
        f"metric_count={len(payload.get('metrics', []))}"
    )

    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        """使用任务级仓储执行元数据导入"""
        return await build_meta_import_service(meta_repo, source_repo).import_metadata(
            MetaConfig.model_validate(payload),
            ImportMode(mode),
            False,
        )

    result = _import_result(run_async(_run_with_metadata_resources(operation)))
    logger.info(
        "元数据导入任务完成: "
        f"mode={mode}, tables={result['tables']['created_count'] + result['tables']['updated_count'] + result['tables']['deleted_count']}, "
        f"columns={result['columns']['created_count'] + result['columns']['updated_count'] + result['columns']['deleted_count']}, "
        f"metrics={result['metrics']['created_count'] + result['metrics']['updated_count'] + result['metrics']['deleted_count']}"
    )
    return result


async def _dispatch_value_indexes() -> dict[str, int]:
    """提交到达每日执行时间的字段取值增量同步任务"""
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=cfg.task_queue.task_time_limit_seconds + 300)
    meta_postgres_client_manager.init()
    try:
        value_count = 0
        while True:
            async with (
                meta_postgres_client_manager.session() as session,
                session.begin(),
            ):
                values = await MetaPGRepo(session).claim_pending_value_index_keys(
                    now=now,
                    stale_before=stale_before,
                    limit=_PERIODIC_BATCH_SIZE,
                )
            if not values:
                break
            try:
                submission = enqueue_column_values(values, mode="incremental")
                logger.info(
                    "提交周期字段取值增量同步批次: "
                    f"task_id={submission.task_id}, column_count={len(values)}, "
                    f"columns={values[:20]}, truncated={len(values) > 20}"
                )
            except Exception as exc:
                async with (
                    meta_postgres_client_manager.session() as session,
                    session.begin(),
                ):
                    await MetaPGRepo(session).fail_value_index_claims(
                        values,
                        error=f"{type(exc).__name__}: {exc}",
                        failed_at=datetime.now(UTC),
                    )
                raise
            value_count += len(values)
            if len(values) < _PERIODIC_BATCH_SIZE:
                break
        logger.info(f"周期字段取值增量同步扫描完成: dispatched_count={value_count}")
        return {"value_count": value_count}
    finally:
        await meta_postgres_client_manager.close()


@celery_app.task(name="dataagent.metadata.dispatch_value_indexes")
def dispatch_value_indexes_task() -> dict[str, int]:
    """提交每日字段取值增量同步任务"""
    return run_async(_dispatch_value_indexes())
