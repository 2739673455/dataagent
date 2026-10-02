"""元数据应用服务组装。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from elasticsearch import AsyncElasticsearch

from app.metadata.contracts import MetadataChangeHandler
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.catalog import MetaCatalogService
from app.metadata.services.import_service import MetaImportService
from app.metadata.services.index import MetaIndexService

if TYPE_CHECKING:
    from app.shared.clients.embedding_client import EmbeddingClient


def build_meta_index_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
    es_client: AsyncElasticsearch,
    embedding_client: EmbeddingClient,
) -> MetaIndexService:
    """创建元数据索引同步服务。"""
    return MetaIndexService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        column_repo=ColumnESRepo(client=es_client),
        metric_repo=MetricESRepo(client=es_client),
        embedding_client=embedding_client,
        value_repo=ValueESRepo(client=es_client),
    )


def build_meta_import_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
    es_client: AsyncElasticsearch,
    embedding_client: EmbeddingClient,
    change_handler: MetadataChangeHandler,
) -> MetaImportService:
    """创建元数据批量导入服务。"""
    return MetaImportService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        meta_index_service=build_meta_index_service(
            meta_repo, source_repo, es_client, embedding_client
        ),
        change_handler=change_handler,
    )


def build_meta_catalog_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
    es_client: AsyncElasticsearch,
    embedding_client: EmbeddingClient,
    change_handler: MetadataChangeHandler,
) -> MetaCatalogService:
    """创建元数据目录管理服务。"""
    return MetaCatalogService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        meta_index_service=build_meta_index_service(
            meta_repo, source_repo, es_client, embedding_client
        ),
        change_handler=change_handler,
    )
