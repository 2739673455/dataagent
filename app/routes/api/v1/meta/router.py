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
from pydantic import ValidationError as PydanticValidationError
from yaml import YAMLError

from app.clients.doris_client_manager import source_doris_client_manager
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.conf.meta_config import MetaConfig, MetadataName
from app.entities.meta import (
    ColumnKey,
    ColumnReference,
    MetricInfo,
)
from app.errors import meta_error
from app.repositories.column_es_repo import ColumnESRepo
from app.repositories.meta_pg_repo import MetaPGRepo
from app.repositories.metric_es_repo import MetricESRepo
from app.repositories.source_doris_repo import SourceDorisRepo
from app.repositories.value_es_repo import ValueESRepo
from app.routes.api.v1.meta import schemas
from app.services.index_service import IndexService
from app.services.meta_import_service import (
    ImportMode,
    MetaImportService,
    ResourceChanges,
)
from app.services.meta_service import MetaService

router = APIRouter(tags=["meta"])
MetadataPath = Annotated[MetadataName, Path()]


def _build_index_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
) -> IndexService:
    """创建索引同步服务"""
    return IndexService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        column_repo=ColumnESRepo(es_client_manager.get_client()),
        metric_repo=MetricESRepo(es_client_manager.get_client()),
        embedding_client=embedding_client_manager.get_client(),
        value_repo=ValueESRepo(es_client_manager.get_client()),
    )


async def get_meta_service() -> AsyncGenerator[MetaService]:
    """创建请求级元数据管理服务"""
    async with (
        meta_postgres_client_manager.session() as meta_session,
        source_doris_client_manager.connection() as source_connection,
    ):
        meta_repo = MetaPGRepo(meta_session)
        source_repo = SourceDorisRepo(source_connection)
        yield MetaService(
            meta_repo=meta_repo,
            source_repo=source_repo,
            index_service=_build_index_service(meta_repo, source_repo),
        )


async def get_index_service() -> AsyncGenerator[IndexService]:
    """创建请求级索引同步服务"""
    async with (
        meta_postgres_client_manager.session() as meta_session,
        source_doris_client_manager.connection() as source_connection,
    ):
        yield _build_index_service(
            MetaPGRepo(meta_session),
            SourceDorisRepo(source_connection),
        )


async def get_meta_import_service() -> AsyncGenerator[MetaImportService]:
    """创建请求级元数据导入服务"""
    async with (
        meta_postgres_client_manager.session() as meta_session,
        source_doris_client_manager.connection() as source_connection,
    ):
        meta_repo = MetaPGRepo(meta_session)
        source_repo = SourceDorisRepo(source_connection)
        yield MetaImportService(
            meta_repo=meta_repo,
            source_repo=source_repo,
            index_service=_build_index_service(meta_repo, source_repo),
        )


MetaServiceDep = Annotated[MetaService, Depends(get_meta_service)]
IndexServiceDep = Annotated[IndexService, Depends(get_index_service)]
MetaImportServiceDep = Annotated[
    MetaImportService,
    Depends(get_meta_import_service),
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
        raise meta_error.InvalidMetadataError(
            detail="Metadata YAML file cannot be empty"
        )

    try:
        raw_config = yaml.safe_load(content.decode("utf-8"))
        return MetaConfig.model_validate(raw_config)
    except UnicodeDecodeError as exc:
        raise meta_error.InvalidMetadataError(
            detail="Metadata YAML file must use UTF-8 encoding",
        ) from exc
    except YAMLError as exc:
        raise meta_error.InvalidMetadataError(
            detail=f"Invalid metadata YAML: {exc}",
        ) from exc
    except PydanticValidationError as exc:
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        raise meta_error.InvalidMetadataError(
            detail="Metadata YAML does not match the required schema",
            extensions={"errors": errors},
        ) from exc


@router.post("/import", response_model=schemas.MetaImportResponse)
async def import_metadata(
    file: Annotated[UploadFile, File(description="元数据 YAML 文件")],
    service: MetaImportServiceDep,
    mode: Annotated[ImportMode, Query(description="导入模式")] = ImportMode.MERGE,
    dry_run: Annotated[bool, Query(description="仅预览变更")] = False,
) -> schemas.MetaImportResponse:
    """从 YAML 文件批量导入元数据"""
    meta_config = await _load_yaml(file)
    result = await service.import_metadata(meta_config, mode, dry_run)

    return schemas.MetaImportResponse(
        mode=result.mode,
        dry_run=result.dry_run,
        tables=_to_import_changes(result.tables),
        columns=_to_import_changes(result.columns),
        metrics=_to_import_changes(result.metrics),
    )


@router.get("/export", response_class=Response)
async def export_metadata(service: MetaServiceDep) -> Response:
    """以 YAML 格式导出全部元数据"""
    meta_config = await service.export_metadata()
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
    service: MetaServiceDep,
) -> list[schemas.TableInfoResponse]:
    """查询全部表元数据"""
    return [
        schemas.TableInfoResponse.model_validate(table_info)
        for table_info in await service.list_table_infos()
    ]


@router.get(
    "/tables/{t_name}/columns",
    response_model=list[schemas.ColumnInfoResponse],
)
async def list_column_infos(
    t_name: MetadataPath,
    service: MetaServiceDep,
) -> list[schemas.ColumnInfoResponse]:
    """查询表下全部字段元数据"""
    return [
        schemas.ColumnInfoResponse.model_validate(column_info)
        for column_info in await service.list_column_infos(t_name)
    ]


@router.get("/metrics", response_model=list[schemas.MetricInfoResponse])
async def list_metric_infos(
    service: MetaServiceDep,
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
    service: MetaServiceDep,
) -> None:
    """新增或更新表元数据"""
    await service.upsert_table_info(
        t_name,
        body.role,
        body.description,
    )


@router.put(
    "/tables/{t_name}/columns/{c_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def upsert_column_info(
    t_name: MetadataPath,
    c_name: MetadataPath,
    body: schemas.ColumnInfoRequest,
    service: MetaServiceDep,
) -> None:
    """新增或更新字段元数据"""
    await service.upsert_column_info(
        t_name=t_name,
        c_name=c_name,
        description=body.description,
        alias=body.alias,
        index_values=body.index_values,
        reference_t_name=body.reference_t_name,
        reference_c_name=body.reference_c_name,
    )


@router.put("/metrics/{metric_name}", status_code=status.HTTP_204_NO_CONTENT)
async def upsert_metric_info(
    metric_name: MetadataPath,
    body: schemas.MetricInfoRequest,
    service: MetaServiceDep,
) -> None:
    """新增或更新指标元数据"""
    await service.upsert_metric_info(
        MetricInfo(
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


@router.delete("/tables/{t_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table_info(t_name: MetadataPath, service: MetaServiceDep) -> None:
    """删除表及其字段元数据和索引"""
    await service.delete_table_info(t_name)


@router.delete(
    "/tables/{t_name}/columns/{c_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_column_info(
    t_name: MetadataPath,
    c_name: MetadataPath,
    service: MetaServiceDep,
) -> None:
    """删除字段元数据和索引"""
    await service.delete_column_info(t_name, c_name)


@router.delete("/metrics/{metric_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metric_info(
    metric_name: MetadataPath,
    service: MetaServiceDep,
) -> None:
    """删除指标元数据和索引"""
    await service.delete_metric_info(metric_name)


@router.post("/columns/sync", response_model=schemas.BatchIndexSyncResponse)
async def sync_column_indexes(
    body: schemas.ColumnIndexSyncRequest,
    service: IndexServiceDep,
) -> schemas.BatchIndexSyncResponse:
    """同步多个字段的语义索引"""
    results = await service.sync_column_indexes(
        [(column.t_name, column.c_name) for column in body.columns]
    )
    return schemas.BatchIndexSyncResponse(
        results=[
            schemas.ColumnIndexSyncResponse(
                t_name=t_name,
                c_name=c_name,
                indexed_count=indexed_count,
            )
            for (t_name, c_name), indexed_count in results.items()
        ]
    )


@router.post("/columns/sync-values", response_model=schemas.BatchIndexSyncResponse)
async def sync_column_values(
    body: schemas.ColumnIndexSyncRequest,
    service: IndexServiceDep,
) -> schemas.BatchIndexSyncResponse:
    """同步多个字段的全部取值索引"""
    results = await service.sync_column_values(
        [(column.t_name, column.c_name) for column in body.columns]
    )
    return schemas.BatchIndexSyncResponse(
        results=[
            schemas.ColumnIndexSyncResponse(
                t_name=t_name,
                c_name=c_name,
                indexed_count=indexed_count,
            )
            for (t_name, c_name), indexed_count in results.items()
        ]
    )


@router.post("/metrics/sync", response_model=schemas.BatchMetricIndexSyncResponse)
async def sync_metric_indexes(
    body: schemas.MetricIndexSyncRequest,
    service: IndexServiceDep,
) -> schemas.BatchMetricIndexSyncResponse:
    """同步多个指标的语义索引"""
    results = await service.sync_metric_indexes(body.metrics)
    return schemas.BatchMetricIndexSyncResponse(
        results=[
            schemas.MetricIndexSyncResponse(
                metric_name=metric_name,
                indexed_count=indexed_count,
            )
            for metric_name, indexed_count in results.items()
        ]
    )
