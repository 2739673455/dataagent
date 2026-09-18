"""元数据接口的请求级依赖组装。"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends

from app.dependencies import WebResourcesDep
from app.identity.api.auth.dependencies import AdminUserDep
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.catalog import MetaCatalogService
from app.metadata.services.import_service import MetaImportService
from app.workflows.providers import (
    build_meta_catalog_service,
    build_meta_import_service,
)


async def _get_meta_catalog_service(
    resources: WebResourcesDep,
    _: AdminUserDep,
) -> AsyncGenerator[MetaCatalogService]:
    """为平台管理员创建完整元数据目录服务。"""
    async with (
        resources.meta.session() as meta_session,
        resources.admin_doris.connection() as source_connection,
    ):
        meta_repo = MetaPGRepo(session=meta_session)
        source_repo = SourceDorisRepo(connection=source_connection)
        yield build_meta_catalog_service(
            meta_repo,
            source_repo,
            resources.es.get_client(),
            resources.embedding.get_client(),
        )


async def _get_meta_import_service(
    resources: WebResourcesDep,
) -> AsyncGenerator[MetaImportService]:
    """创建请求级元数据导入服务。"""
    async with (
        resources.meta.session() as meta_session,
        resources.admin_doris.connection() as source_connection,
    ):
        meta_repo = MetaPGRepo(session=meta_session)
        source_repo = SourceDorisRepo(connection=source_connection)
        yield build_meta_import_service(
            meta_repo,
            source_repo,
            resources.es.get_client(),
            resources.embedding.get_client(),
        )


MetaCatalogServiceDep = Annotated[
    MetaCatalogService,
    Depends(_get_meta_catalog_service),
]
MetaImportServiceDep = Annotated[
    MetaImportService,
    Depends(_get_meta_import_service),
]
