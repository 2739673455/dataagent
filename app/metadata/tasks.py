"""元数据导入与索引同步后台任务"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.metadata.providers import (
    build_meta_import_service,
    build_meta_index_service,
)
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.search_models import (
    SemanticIndexSyncResult,
    ValueIndexSyncResult,
)
from app.metadata.services.import_service import ImportMode, MetaImportResult
from app.shared.clients.doris_client_manager import admin_doris_client_manager
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import meta_postgres_client_manager
from app.shared.config.app_config import cfg
from app.shared.config.meta_config import MetaConfig
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.runner import run_async
from app.shared.tasks.submission import TaskSubmission

_PERIODIC_BATCH_SIZE = 500


async def _run_with_metadata_resources[T](
    operation: Callable[[MetaPGRepo, SourceDorisRepo], Awaitable[T]],
) -> T:
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
    return [
        {"t_name": t_name, "c_name": c_name, **asdict(result)}
        for (t_name, c_name), result in results.items()
    ]


def _column_value_results(
    results: dict[tuple[str, str], ValueIndexSyncResult],
) -> list[dict[str, Any]]:
    return [
        {"t_name": t_name, "c_name": c_name, **asdict(result)}
        for (t_name, c_name), result in results.items()
    ]


def _metric_semantic_results(
    results: dict[str, SemanticIndexSyncResult],
) -> list[dict[str, Any]]:
    return [
        {"metric_name": metric_name, **asdict(result)}
        for metric_name, result in results.items()
    ]


def _format_key(key: str | tuple[str, str]) -> str:
    return ".".join(key) if isinstance(key, tuple) else key


def _import_result(result: MetaImportResult) -> dict[str, Any]:
    def changes(value: Any) -> dict[str, Any]:
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
    task = celery_app.send_task(
        name,
        args=args,
        queue="metadata-index",
        routing_key="metadata-index",
    )
    return TaskSubmission(task_id=task.id)


def enqueue_table_indexes(table_names: list[str]) -> TaskSubmission:
    return _submit("dataagent.metadata.sync_table_indexes", [table_names])


def enqueue_table_values(
    table_names: list[str],
    *,
    force_reconcile: bool = True,
) -> TaskSubmission:
    return _submit(
        "dataagent.metadata.sync_table_values",
        [table_names, force_reconcile],
    )


def enqueue_column_indexes(column_keys: list[tuple[str, str]]) -> TaskSubmission:
    return _submit("dataagent.metadata.sync_column_indexes", [column_keys])


def enqueue_column_values(
    column_keys: list[tuple[str, str]],
    *,
    force_reconcile: bool = True,
) -> TaskSubmission:
    return _submit(
        "dataagent.metadata.sync_column_values",
        [column_keys, force_reconcile],
    )


def enqueue_metric_indexes(metric_names: list[str]) -> TaskSubmission:
    return _submit("dataagent.metadata.sync_metric_indexes", [metric_names])


def enqueue_import(
    meta_config: MetaConfig,
    mode: ImportMode,
) -> TaskSubmission:
    return _submit(
        "dataagent.metadata.import",
        [meta_config.model_dump(mode="json"), mode.value],
    )


@celery_app.task(
    name="dataagent.metadata.sync_table_indexes",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def sync_table_indexes_task(table_names: list[str]) -> dict[str, Any]:
    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        return await build_meta_index_service(meta_repo, source_repo).sync_table_indexes(
            table_names
        )

    return {
        "results": _column_semantic_results(
            run_async(_run_with_metadata_resources(operation))
        )
    }


@celery_app.task(
    name="dataagent.metadata.sync_table_values",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def sync_table_values_task(
    table_names: list[str],
    force_reconcile: bool,
) -> dict[str, Any]:
    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        return await build_meta_index_service(meta_repo, source_repo).sync_table_values(
            table_names,
            force_reconcile=force_reconcile,
        )

    return {
        "results": _column_value_results(
            run_async(_run_with_metadata_resources(operation))
        )
    }


@celery_app.task(
    name="dataagent.metadata.sync_column_indexes",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def sync_column_indexes_task(column_keys: list[list[str]]) -> dict[str, Any]:
    keys = [(t_name, c_name) for t_name, c_name in column_keys]

    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        return await build_meta_index_service(meta_repo, source_repo).sync_column_indexes(
            keys
        )

    return {
        "results": _column_semantic_results(
            run_async(_run_with_metadata_resources(operation))
        )
    }


@celery_app.task(
    name="dataagent.metadata.sync_column_values",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def sync_column_values_task(
    column_keys: list[list[str]],
    force_reconcile: bool,
) -> dict[str, Any]:
    keys = [(t_name, c_name) for t_name, c_name in column_keys]

    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        return await build_meta_index_service(meta_repo, source_repo).sync_column_values(
            keys,
            force_reconcile=force_reconcile,
        )

    return {
        "results": _column_value_results(
            run_async(_run_with_metadata_resources(operation))
        )
    }


@celery_app.task(
    name="dataagent.metadata.sync_metric_indexes",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def sync_metric_indexes_task(metric_names: list[str]) -> dict[str, Any]:
    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        return await build_meta_index_service(meta_repo, source_repo).sync_metric_indexes(
            metric_names
        )

    return {
        "results": _metric_semantic_results(
            run_async(_run_with_metadata_resources(operation))
        )
    }


@celery_app.task(
    name="dataagent.metadata.import",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def import_metadata_task(payload: dict[str, Any], mode: str) -> dict[str, Any]:
    async def operation(meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> Any:
        return await build_meta_import_service(meta_repo, source_repo).import_metadata(
            MetaConfig.model_validate(payload),
            ImportMode(mode),
            False,
        )

    return _import_result(run_async(_run_with_metadata_resources(operation)))


async def _dispatch_value_indexes() -> dict[str, int]:
    """提交到达每日执行时间的字段取值增量同步任务"""
    now = datetime.now(UTC)
    stale_before = now - timedelta(
        seconds=cfg.task_queue.task_time_limit_seconds + 300
    )
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
                enqueue_column_values(values, force_reconcile=False)
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
        return {"value_count": value_count}
    finally:
        await meta_postgres_client_manager.close()


@celery_app.task(name="dataagent.metadata.dispatch_value_indexes")
def dispatch_value_indexes_task() -> dict[str, int]:
    """提交每日字段取值增量同步任务"""
    return run_async(_dispatch_value_indexes())
