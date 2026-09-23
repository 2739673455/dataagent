"""跨领域元数据变更用例组装，供 HTTP 和 Worker 共用。"""

from elasticsearch import AsyncElasticsearch

from app.metadata.providers import build_meta_index_service
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.catalog import MetaCatalogService
from app.metadata.services.import_service import MetaImportService
from app.metadata.task_scheduler import CeleryMetadataSemanticIndexScheduler
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.workflows.metadata_changes import MetadataChangeWorkflow


def build_meta_import_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
    es_client: AsyncElasticsearch,
    embedding_client: EmbeddingClient,
) -> MetaImportService:
    """创建元数据批量导入服务。"""
    return MetaImportService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        meta_index_service=build_meta_index_service(
            meta_repo, source_repo, es_client, embedding_client
        ),
        change_handler=MetadataChangeWorkflow(
            CeleryMetadataSemanticIndexScheduler(),
        ),
    )


def build_meta_catalog_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
    es_client: AsyncElasticsearch,
    embedding_client: EmbeddingClient,
) -> MetaCatalogService:
    """创建元数据目录管理服务。"""
    return MetaCatalogService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        meta_index_service=build_meta_index_service(
            meta_repo, source_repo, es_client, embedding_client
        ),
        change_handler=MetadataChangeWorkflow(
            CeleryMetadataSemanticIndexScheduler(),
        ),
    )
