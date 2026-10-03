"""元数据导入与索引同步后台任务。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from elasticsearch import AsyncElasticsearch
from loguru import logger

from app.metadata.catalog.importer import (
    ImportMode,
    MetaImportResult,
    MetaImportService,
)
from app.metadata.config import MetaConfig
from app.metadata.contracts import (
    RequestedValueIndexSyncMode,
    SemanticIndexSyncResult,
    ValueIndexSyncResult,
)
from app.metadata.indexing import MetaIndexService
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.task_scheduler import (
    DISPATCH_VALUE_INDEXES_TASK,
    IMPORT_METADATA_TASK,
    SYNC_COLUMN_INDEXES_TASK,
    SYNC_COLUMN_VALUES_TASK,
    SYNC_METRIC_INDEXES_TASK,
    SYNC_TABLE_INDEXES_TASK,
    SYNC_TABLE_VALUES_TASK,
    enqueue_column_values,
)
from app.shared.async_runtime import run_async
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.embedding_client import EmbeddingClient
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.tasks.celery_app import celery_app
from app.workflows import build_metadata_change_workflow

_PERIODIC_BATCH_SIZE = 50


@celery_app.task(
    name=SYNC_TABLE_INDEXES_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_table_indexes_task(table_names: list[str]) -> dict[str, Any]:
    """执行多个表的字段语义索引同步。"""
    logger.info(
        "开始执行表字段语义索引同步任务: "
        f"table_count={len(table_names)}, tables={table_names[:20]}, "
        f"truncated={len(table_names) > 20}"
    )

    async def operation():
        """在任务资源范围内执行表字段语义索引同步。"""
        async with _metadata_resources() as (_, _, index):
            return await index.sync_table_indexes(table_names)

    results = _column_semantic_results(run_async(operation()))
    logger.info(
        "表字段语义索引同步任务完成: "
        f"table_count={len(table_names)}, result_count={len(results)}"
    )
    return {"results": results}


@celery_app.task(
    name=SYNC_TABLE_VALUES_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_table_values_task(
    table_names: list[str],
    mode: RequestedValueIndexSyncMode,
) -> dict[str, Any]:
    """执行多个表的字段取值索引同步。"""
    logger.info(
        "开始执行表字段取值索引同步任务: "
        f"table_count={len(table_names)}, mode={mode}, "
        f"tables={table_names[:20]}, truncated={len(table_names) > 20}"
    )

    async def operation():
        """在任务资源范围内执行表字段取值索引同步。"""
        async with _metadata_resources() as (_, _, index):
            return await index.sync_table_values(
                table_names,
                mode=mode,
            )

    results = _column_value_results(run_async(operation()))
    logger.info(
        "表字段取值索引同步任务完成: "
        f"table_count={len(table_names)}, result_count={len(results)}, "
        f"mode={mode}"
    )
    return {"results": results}


@celery_app.task(
    name=SYNC_COLUMN_INDEXES_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_column_indexes_task(column_keys: list[list[str]]) -> dict[str, Any]:
    """执行指定字段的语义索引同步。"""
    keys = [(t_name, c_name) for t_name, c_name in column_keys]
    logger.info(
        "开始执行字段语义索引同步任务: "
        f"column_count={len(keys)}, columns={keys[:20]}, "
        f"truncated={len(keys) > 20}"
    )

    async def operation():
        """在任务资源范围内执行字段语义索引同步。"""
        async with _metadata_resources() as (_, _, index):
            return await index.sync_column_indexes(keys)

    results = _column_semantic_results(run_async(operation()))
    logger.info(
        "字段语义索引同步任务完成: "
        f"column_count={len(keys)}, result_count={len(results)}"
    )
    return {"results": results}


@celery_app.task(
    name=SYNC_COLUMN_VALUES_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_column_values_task(
    column_keys: list[list[str]],
    mode: RequestedValueIndexSyncMode,
) -> dict[str, Any]:
    """执行指定字段的取值索引同步。"""
    keys = [(t_name, c_name) for t_name, c_name in column_keys]
    logger.info(
        "开始执行字段取值索引同步任务: "
        f"column_count={len(keys)}, mode={mode}, "
        f"columns={keys[:20]}, truncated={len(keys) > 20}"
    )

    async def operation():
        """在任务资源范围内执行字段取值索引同步。"""
        async with _metadata_resources() as (_, _, index):
            return await index.sync_column_values(
                keys,
                mode=mode,
            )

    results = _column_value_results(run_async(operation()))
    logger.info(
        "字段取值索引同步任务完成: "
        f"column_count={len(keys)}, result_count={len(results)}, "
        f"mode={mode}"
    )
    return {"results": results}


@celery_app.task(
    name=SYNC_METRIC_INDEXES_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_metric_indexes_task(metric_names: list[str]) -> dict[str, Any]:
    """执行指定指标的语义索引同步。"""
    logger.info(
        "开始执行指标语义索引同步任务: "
        f"metric_count={len(metric_names)}, metrics={metric_names[:20]}, "
        f"truncated={len(metric_names) > 20}"
    )

    async def operation():
        """在任务资源范围内执行指标语义索引同步。"""
        async with _metadata_resources() as (_, _, index):
            return await index.sync_metric_indexes(metric_names)

    results = _metric_semantic_results(run_async(operation()))
    logger.info(
        "指标语义索引同步任务完成: "
        f"metric_count={len(metric_names)}, result_count={len(results)}"
    )
    return {"results": results}


@celery_app.task(
    name=IMPORT_METADATA_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def import_metadata_task(payload: dict[str, Any], mode: str) -> dict[str, Any]:
    """执行元数据配置导入并返回变更摘要。"""
    logger.info(
        "开始执行元数据导入任务: "
        f"mode={mode}, table_count={len(payload.get('tables', []))}, "
        f"metric_count={len(payload.get('metrics', []))}"
    )

    async def operation() -> MetaImportResult:
        """在任务资源范围内导入目录并处理后续变更。"""
        async with _metadata_resources() as (meta_repo, source_repo, index):
            query_postgres = PostgresClientManager(cfg.meta_postgresql)
            try:
                return await MetaImportService(
                    meta_repo=meta_repo,
                    source_repo=source_repo,
                    meta_index_service=index,
                    change_handler=build_metadata_change_workflow(query_postgres),
                ).import_metadata(
                    MetaConfig.model_validate(payload), ImportMode(mode), False
                )
            finally:
                await query_postgres.close()

    result = _import_result(run_async(operation()))
    logger.info(
        "元数据导入任务完成: "
        f"mode={mode}, tables={result['tables']['created_count'] + result['tables']['updated_count'] + result['tables']['deleted_count']}, "
        f"columns={result['columns']['created_count'] + result['columns']['updated_count'] + result['columns']['deleted_count']}, "
        f"metrics={result['metrics']['created_count'] + result['metrics']['updated_count'] + result['metrics']['deleted_count']}"
    )
    return result


@celery_app.task(name=DISPATCH_VALUE_INDEXES_TASK)
def dispatch_value_indexes_task() -> dict[str, int]:
    """提交符合条件的字段取值同步或清理任务。"""
    return run_async(_dispatch_value_indexes())


@asynccontextmanager
async def _metadata_resources() -> AsyncGenerator[
    tuple[MetaPGRepo, SourceDorisRepo, MetaIndexService]
]:
    """为一次后台任务创建仓储和索引服务，并在结束时释放资源。"""
    async with AsyncExitStack() as stack:
        embedding = EmbeddingClient(cfg.embedding)
        stack.push_async_callback(embedding.close)
        es = AsyncElasticsearch(
            hosts=[f"http://{cfg.elasticsearch.host}:{cfg.elasticsearch.port}"]
        )
        stack.push_async_callback(es.close)
        postgres = PostgresClientManager(cfg.meta_postgresql)
        stack.push_async_callback(postgres.close)
        doris = DorisClientManager(cfg.doris)
        stack.push_async_callback(doris.close)
        async with postgres.session() as session, doris.engine.connect() as connection:
            meta_repo = MetaPGRepo(session)
            source_repo = SourceDorisRepo(connection)
            yield (
                meta_repo,
                source_repo,
                MetaIndexService(
                    meta_repo=meta_repo,
                    source_repo=source_repo,
                    column_repo=ColumnESRepo(es),
                    metric_repo=MetricESRepo(es),
                    value_repo=ValueESRepo(es),
                    embedding_client=embedding,
                ),
            )


async def _dispatch_value_indexes() -> dict[str, int]:
    """扫描符合状态和时间条件的字段，分批提交取值同步任务。"""
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=cfg.task_queue.task_time_limit_seconds + 300)
    postgres = PostgresClientManager(cfg.meta_postgresql)
    try:
        value_count = 0
        while True:
            async with (
                postgres.session() as session,
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
                    postgres.session() as session,
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
        await postgres.close()


def _column_semantic_results(
    results: dict[tuple[str, str], SemanticIndexSyncResult],
) -> list[dict[str, Any]]:
    """将字段语义索引同步结果转换为任务响应结构。"""
    return [
        {"t_name": t_name, "c_name": c_name, **asdict(result)}
        for (t_name, c_name), result in results.items()
    ]


def _column_value_results(
    results: dict[tuple[str, str], ValueIndexSyncResult],
) -> list[dict[str, Any]]:
    """将字段取值索引同步结果转换为任务响应结构。"""
    return [
        {"t_name": t_name, "c_name": c_name, **asdict(result)}
        for (t_name, c_name), result in results.items()
    ]


def _metric_semantic_results(
    results: dict[str, SemanticIndexSyncResult],
) -> list[dict[str, Any]]:
    """将指标语义索引同步结果转换为任务响应结构。"""
    return [
        {"metric_name": metric_name, **asdict(result)}
        for metric_name, result in results.items()
    ]


def _import_result(result: MetaImportResult) -> dict[str, Any]:
    """汇总元数据导入结果中的各类资源变更。"""

    def changes(value: Any) -> dict[str, Any]:
        """统计单类资源的新增、更新和删除明细。"""
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


def _format_key(key: str | tuple[str, str]) -> str:
    """将元数据资源键格式化为可序列化文本。"""
    return ".".join(key) if isinstance(key, tuple) else key
