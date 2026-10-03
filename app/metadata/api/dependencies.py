"""元数据接口的请求级会话与业务服务。"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.dependencies import AdminUserDep, WebResourcesDep
from app.metadata.catalog.importer import MetaImportService
from app.metadata.catalog.service import MetaCatalogService
from app.metadata.indexing import MetaIndexService
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.workflows import build_metadata_change_workflow


async def _get_meta_session(
    resources: WebResourcesDep,
    _: AdminUserDep,
) -> AsyncGenerator[AsyncSession]:
    """为管理员请求创建元数据数据库会话。"""
    async with resources.meta.session() as session:
        yield session


async def _get_source_connection(
    resources: WebResourcesDep,
    _: AdminUserDep,
) -> AsyncGenerator[AsyncConnection]:
    """为目录校验和示例读取提供 Doris 连接。"""
    async with resources.admin_doris.engine.connect() as connection:
        yield connection


MetaSessionDep = Annotated[AsyncSession, Depends(_get_meta_session)]
SourceConnectionDep = Annotated[AsyncConnection, Depends(_get_source_connection)]


def _get_meta_index_service(
    session: MetaSessionDep,
    connection: SourceConnectionDep,
    resources: WebResourcesDep,
) -> MetaIndexService:
    """使用当前请求的会话和共享客户端创建索引服务。"""
    return MetaIndexService(
        meta_repo=MetaPGRepo(session),
        source_repo=SourceDorisRepo(connection),
        column_repo=ColumnESRepo(resources.es),
        metric_repo=MetricESRepo(resources.es),
        value_repo=ValueESRepo(resources.es),
        embedding_client=resources.embedding,
    )


MetaIndexServiceDep = Annotated[MetaIndexService, Depends(_get_meta_index_service)]


def _get_meta_catalog_service(
    session: MetaSessionDep,
    connection: SourceConnectionDep,
    index: MetaIndexServiceDep,
    resources: WebResourcesDep,
) -> MetaCatalogService:
    """创建目录管理服务并接入元数据变更工作流。"""
    return MetaCatalogService(
        meta_repo=MetaPGRepo(session),
        source_repo=SourceDorisRepo(connection),
        meta_index_service=index,
        change_handler=build_metadata_change_workflow(resources.query),
    )


def _get_meta_import_service(
    session: MetaSessionDep,
    connection: SourceConnectionDep,
    index: MetaIndexServiceDep,
    resources: WebResourcesDep,
) -> MetaImportService:
    """创建批量导入服务并接入元数据变更工作流。"""
    return MetaImportService(
        meta_repo=MetaPGRepo(session),
        source_repo=SourceDorisRepo(connection),
        meta_index_service=index,
        change_handler=build_metadata_change_workflow(resources.query),
    )


MetaCatalogServiceDep = Annotated[
    MetaCatalogService, Depends(_get_meta_catalog_service)
]
MetaImportServiceDep = Annotated[MetaImportService, Depends(_get_meta_import_service)]
