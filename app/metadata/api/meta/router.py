"""元数据管理与索引同步路由"""

from collections.abc import AsyncGenerator
from typing import Annotated

import yaml
from fastapi import (
    APIRouter,
    Depends,
    File,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)
from loguru import logger
from pydantic import ValidationError as PydanticValidationError
from yaml import YAMLError

from app.identity.api.auth.dependencies import (
    AdminUserDep,
)
from app.metadata import errors as meta_error
from app.metadata.api.meta import schemas
from app.metadata.models.catalog import (
    ColumnKey,
    ColumnReference,
    MetricInfo,
)
from app.metadata.providers import (
    build_meta_import_service,
    build_meta_index_service,
)
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.catalog import MetaCatalogService
from app.metadata.services.import_service import (
    ImportMode,
    MetaImportService,
    ResourceChanges,
)
from app.metadata.task_scheduler import CeleryMetadataSemanticIndexScheduler
from app.metadata.tasks import (
    enqueue_column_indexes,
    enqueue_column_values,
    enqueue_import,
    enqueue_metric_indexes,
    enqueue_table_indexes,
    enqueue_table_values,
)
from app.query.providers import build_query_experience_service
from app.shared.clients.doris_client_manager import admin_doris_client_manager
from app.shared.clients.postgres_client_manager import meta_postgres_client_manager
from app.shared.config.meta_config import MetaConfig, MetadataName
from app.shared.tasks.schemas import TaskAcceptedResponse

router = APIRouter(tags=["meta"])
MetadataPath = Annotated[MetadataName, Path()]


async def _get_meta_catalog_service(
    _: AdminUserDep,
) -> AsyncGenerator[MetaCatalogService]:
    """为平台管理员创建完整元数据目录服务"""
    async with (
        meta_postgres_client_manager.session() as meta_session,
        admin_doris_client_manager.connection() as source_connection,
    ):
        meta_repo = MetaPGRepo(session=meta_session)
        source_repo = SourceDorisRepo(connection=source_connection)
        yield MetaCatalogService(
            meta_repo=meta_repo,
            source_repo=source_repo,
            meta_index_service=build_meta_index_service(
                meta_repo=meta_repo,
                source_repo=source_repo,
            ),
            asset_invalidator=build_query_experience_service(meta_session),
            semantic_index_scheduler=CeleryMetadataSemanticIndexScheduler(),
        )


async def _get_meta_import_service() -> AsyncGenerator[MetaImportService]:
    """创建请求级元数据导入服务"""
    async with (
        meta_postgres_client_manager.session() as meta_session,
        admin_doris_client_manager.connection() as source_connection,
    ):
        meta_repo = MetaPGRepo(session=meta_session)
        source_repo = SourceDorisRepo(connection=source_connection)
        yield build_meta_import_service(meta_repo, source_repo)


MetaCatalogServiceDep = Annotated[
    MetaCatalogService,
    Depends(_get_meta_catalog_service),
]
MetaImportServiceDep = Annotated[
    MetaImportService,
    Depends(_get_meta_import_service),
]


def _format_resource_key(key: str | ColumnKey) -> str:
    """将资源主键转换为响应文本"""
    return ".".join(key) if isinstance(key, tuple) else key


def _to_import_changes[T: (str, tuple[str, str])](
    changes: ResourceChanges[T],
) -> schemas.ResourceImportChanges:
    """转换元数据导入变更响应"""
    return schemas.ResourceImportChanges(
        created_count=len(changes.created),
        updated_count=len(changes.updated),
        deleted_count=len(changes.deleted),
        created_keys=[_format_resource_key(key) for key in changes.created],
        updated_keys=[_format_resource_key(key) for key in changes.updated],
        deleted_keys=[_format_resource_key(key) for key in changes.deleted],
    )


async def _load_yaml(file: UploadFile) -> MetaConfig:
    """读取并校验上传的 YAML 元数据配置"""
    try:
        content = await file.read()
    finally:
        await file.close()
    if not content:
        raise meta_error.InvalidMetadataError(detail="元数据 YAML 文件不能为空")

    try:
        raw_config = yaml.safe_load(content.decode("utf-8"))
        return MetaConfig.model_validate(raw_config)
    except UnicodeDecodeError as exc:
        raise meta_error.InvalidMetadataError(
            detail="元数据 YAML 文件必须使用 UTF-8 编码",
        ) from exc
    except YAMLError as exc:
        raise meta_error.InvalidMetadataError(
            detail=f"元数据 YAML 格式解析失败: {exc}",
        ) from exc
    except PydanticValidationError as exc:
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        raise meta_error.InvalidMetadataError(
            detail="元数据 YAML 结构不符合规范要求",
            extensions={"errors": errors},
        ) from exc


@router.post(
    "/import",
    response_model=schemas.MetaImportResponse | TaskAcceptedResponse,
)
async def import_metadata(
    file: Annotated[UploadFile, File(description="元数据 YAML 文件")],
    service: MetaImportServiceDep,
    current_admin: AdminUserDep,
    mode: Annotated[ImportMode, Query(description="导入模式")] = ImportMode.MERGE,
    dry_run: Annotated[bool, Query(description="仅预览变更")] = False,
) -> schemas.MetaImportResponse | TaskAcceptedResponse:
    """从 YAML 文件批量导入元数据"""
    meta_config = await _load_yaml(file=file)
    if not dry_run:
        submission = enqueue_import(meta_config, mode)
        logger.info(
            f"管理员提交元数据导入任务: operator_id={current_admin.id}, "
            f"task_id={submission.task_id}, mode={mode.value}, "
            f"table_count={len(meta_config.tables)}, "
            f"metric_count={len(meta_config.metrics)}"
        )
        return TaskAcceptedResponse(task_id=submission.task_id)

    result = await service.import_metadata(meta_config, mode, True)
    logger.info(
        f"管理员完成元数据导入预览: operator_id={current_admin.id}, "
        f"mode={mode.value}, table_changes={len(result.tables.created) + len(result.tables.updated) + len(result.tables.deleted)}, "
        f"column_changes={len(result.columns.created) + len(result.columns.updated) + len(result.columns.deleted)}, "
        f"metric_changes={len(result.metrics.created) + len(result.metrics.updated) + len(result.metrics.deleted)}"
    )

    return schemas.MetaImportResponse(
        mode=result.mode,
        dry_run=result.dry_run,
        tables=_to_import_changes(changes=result.tables),
        columns=_to_import_changes(changes=result.columns),
        metrics=_to_import_changes(changes=result.metrics),
    )


@router.get("/export", response_class=Response)
async def export_metadata(
    service: MetaCatalogServiceDep,
    current_admin: AdminUserDep,
) -> Response:
    """以 YAML 格式导出全部元数据"""
    meta_config = await service.export_metadata()
    logger.info(
        f"管理员导出元数据: operator_id={current_admin.id}, "
        f"table_count={len(meta_config.tables)}, metric_count={len(meta_config.metrics)}"
    )
    content = yaml.safe_dump(
        meta_config.model_dump(mode="json", exclude_none=True),
        allow_unicode=True,
        sort_keys=False,
    )
    return Response(
        content=content,
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="metadata.yaml"'},
    )


@router.get("/tables", response_model=list[schemas.TableInfoResponse])
async def list_table_infos(
    service: MetaCatalogServiceDep,
    _: AdminUserDep,
) -> list[schemas.TableInfoResponse]:
    """查询全部表元数据"""
    return [
        schemas.TableInfoResponse.model_validate(table_info)
        for table_info in await service.list_table_infos()
    ]


@router.get("/source-tables", response_model=list[str])
async def list_source_tables(
    service: MetaCatalogServiceDep,
    _: AdminUserDep,
) -> list[str]:
    """查询底层 Doris 数据源中的所有物理表名"""
    return await service.list_source_tables()


@router.get(
    "/tables/{t_name}/columns",
    response_model=list[schemas.ColumnInfoResponse],
)
async def list_column_infos(
    t_name: MetadataPath,
    service: MetaCatalogServiceDep,
    _: AdminUserDep,
) -> list[schemas.ColumnInfoResponse]:
    """查询表下全部字段元数据"""
    return [
        schemas.ColumnInfoResponse.model_validate(column_info)
        for column_info in await service.list_column_infos(t_name=t_name)
    ]


@router.get("/metrics", response_model=list[schemas.MetricInfoResponse])
async def list_metric_infos(
    service: MetaCatalogServiceDep,
    _: AdminUserDep,
) -> list[schemas.MetricInfoResponse]:
    """查询全部指标元数据"""
    return [
        schemas.MetricInfoResponse.model_validate(metric_info)
        for metric_info in await service.list_metric_infos()
    ]


@router.put("/tables/{t_name}", status_code=status.HTTP_204_NO_CONTENT)
async def upsert_table_info(
    t_name: MetadataPath,
    body: schemas.TableInfoRequest,
    service: MetaCatalogServiceDep,
    current_admin: AdminUserDep,
) -> None:
    """新增或更新表元数据"""
    await service.upsert_table_info(
        t_name=t_name,
        role=body.role,
        description=body.description,
        value_index_cursor_column=body.value_index_cursor_column,
    )
    logger.info(
        f"管理员新增或更新表元数据: operator_id={current_admin.id}, "
        f"table={t_name}, role={body.role}, "
        "value_index_cursor_column="
        f"{body.value_index_cursor_column}"
    )


@router.put(
    "/tables/{t_name}/columns/{c_name}",
    response_model=schemas.SemanticIndexUpsertResponse,
)
async def upsert_column_info(
    t_name: MetadataPath,
    c_name: MetadataPath,
    body: schemas.ColumnInfoRequest,
    service: MetaCatalogServiceDep,
    current_admin: AdminUserDep,
) -> schemas.SemanticIndexUpsertResponse:
    """新增或更新字段元数据"""
    submission = await service.upsert_column_info(
        t_name=t_name,
        c_name=c_name,
        description=body.description,
        alias=body.alias,
        index_values=body.index_values,
        reference_t_name=body.reference_t_name,
        reference_c_name=body.reference_c_name,
    )
    logger.info(
        f"管理员新增或更新字段元数据: operator_id={current_admin.id}, "
        f"column={t_name}.{c_name}, index_values={body.index_values}, "
        f"reference={body.reference_t_name}.{body.reference_c_name}, "
        "semantic_index_task_id="
        f"{submission.task_id if submission is not None else None}"
    )
    return schemas.SemanticIndexUpsertResponse(
        semantic_index_task_id=(submission.task_id if submission is not None else None)
    )


@router.put(
    "/metrics/{metric_name}",
    response_model=schemas.SemanticIndexUpsertResponse,
)
async def upsert_metric_info(
    metric_name: MetadataPath,
    body: schemas.MetricInfoRequest,
    service: MetaCatalogServiceDep,
    current_admin: AdminUserDep,
) -> schemas.SemanticIndexUpsertResponse:
    """新增或更新指标元数据"""
    submission = await service.upsert_metric_info(
        metric_info=MetricInfo(
            name=metric_name,
            description=body.description,
            relevant_columns=[
                ColumnReference(
                    t_name=reference.t_name,
                    c_name=reference.c_name,
                )
                for reference in body.relevant_columns
            ],
            alias=body.alias,
        )
    )
    logger.info(
        f"管理员新增或更新指标元数据: operator_id={current_admin.id}, "
        f"metric={metric_name}, column_count={len(body.relevant_columns)}, "
        f"alias_count={len(body.alias)}, "
        "semantic_index_task_id="
        f"{submission.task_id if submission is not None else None}"
    )
    return schemas.SemanticIndexUpsertResponse(
        semantic_index_task_id=(submission.task_id if submission is not None else None)
    )


@router.post("/tables/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tables(
    body: schemas.TableBatchDeleteRequest,
    service: MetaCatalogServiceDep,
    current_admin: AdminUserDep,
) -> None:
    """批量删除表及其字段元数据和索引"""
    await service.delete_tables(table_names=body.tables)
    logger.info(
        f"管理员批量删除表元数据: operator_id={current_admin.id}, tables={body.tables}"
    )


@router.post("/columns/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_columns(
    body: schemas.ColumnBatchDeleteRequest,
    service: MetaCatalogServiceDep,
    current_admin: AdminUserDep,
) -> None:
    """批量删除字段元数据和索引"""
    await service.delete_columns(
        column_keys=[(column.t_name, column.c_name) for column in body.columns]
    )
    logger.info(
        f"管理员批量删除字段元数据: operator_id={current_admin.id}, "
        f"columns={[f'{column.t_name}.{column.c_name}' for column in body.columns]}"
    )


@router.post("/metrics/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metrics(
    body: schemas.MetricBatchDeleteRequest,
    service: MetaCatalogServiceDep,
    current_admin: AdminUserDep,
) -> None:
    """批量删除指标元数据和索引"""
    await service.delete_metrics(metric_names=body.metrics)
    logger.info(
        f"管理员批量删除指标元数据: operator_id={current_admin.id}, "
        f"metrics={body.metrics}"
    )


@router.post("/tables/sync", response_model=TaskAcceptedResponse)
async def sync_table_indexes(
    body: schemas.TableIndexSyncRequest,
    current_admin: AdminUserDep,
) -> TaskAcceptedResponse:
    """同步多个表的全部字段语义索引"""
    submission = enqueue_table_indexes(body.tables)
    logger.info(
        f"管理员提交表字段语义索引同步任务: operator_id={current_admin.id}, "
        f"task_id={submission.task_id}, tables={body.tables}"
    )
    return TaskAcceptedResponse(task_id=submission.task_id)


@router.post("/tables/sync-values", response_model=TaskAcceptedResponse)
async def sync_table_values(
    body: schemas.TableValueIndexSyncRequest,
    current_admin: AdminUserDep,
) -> TaskAcceptedResponse:
    """同步多个表中已开启字段的取值索引"""
    submission = enqueue_table_values(body.tables, mode=body.mode)
    logger.info(
        f"管理员提交表字段取值索引同步任务: operator_id={current_admin.id}, "
        f"task_id={submission.task_id}, mode={body.mode}, tables={body.tables}"
    )
    return TaskAcceptedResponse(task_id=submission.task_id)


@router.post("/columns/sync", response_model=TaskAcceptedResponse)
async def sync_column_indexes(
    body: schemas.ColumnIndexSyncRequest,
    current_admin: AdminUserDep,
) -> TaskAcceptedResponse:
    """同步多个字段的语义索引"""
    submission = enqueue_column_indexes(
        [(column.t_name, column.c_name) for column in body.columns]
    )
    logger.info(
        f"管理员提交字段语义索引同步任务: operator_id={current_admin.id}, "
        f"task_id={submission.task_id}, "
        f"columns={[f'{column.t_name}.{column.c_name}' for column in body.columns]}"
    )
    return TaskAcceptedResponse(task_id=submission.task_id)


@router.post("/columns/sync-values", response_model=TaskAcceptedResponse)
async def sync_column_values(
    body: schemas.ColumnValueIndexSyncRequest,
    current_admin: AdminUserDep,
) -> TaskAcceptedResponse:
    """同步多个已开启字段的取值索引"""
    submission = enqueue_column_values(
        [(column.t_name, column.c_name) for column in body.columns],
        mode=body.mode,
    )
    logger.info(
        f"管理员提交字段取值索引同步任务: operator_id={current_admin.id}, "
        f"task_id={submission.task_id}, mode={body.mode}, "
        f"columns={[f'{column.t_name}.{column.c_name}' for column in body.columns]}"
    )
    return TaskAcceptedResponse(task_id=submission.task_id)


@router.post("/metrics/sync", response_model=TaskAcceptedResponse)
async def sync_metric_indexes(
    body: schemas.MetricIndexSyncRequest,
    current_admin: AdminUserDep,
) -> TaskAcceptedResponse:
    """同步多个指标的语义索引"""
    submission = enqueue_metric_indexes(body.metrics)
    logger.info(
        f"管理员提交指标语义索引同步任务: operator_id={current_admin.id}, "
        f"task_id={submission.task_id}, metrics={body.metrics}"
    )
    return TaskAcceptedResponse(task_id=submission.task_id)
