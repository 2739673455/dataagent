"""元数据应用服务组装。"""

from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.catalog import MetaCatalogService
from app.metadata.services.import_service import MetaImportService
from app.metadata.services.index import MetaIndexService
from app.metadata.task_scheduler import CeleryMetadataSemanticIndexScheduler
from app.query.providers import build_query_experience_invalidation_service
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager


def build_meta_index_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
) -> MetaIndexService:
    """创建元数据索引同步服务。"""
    client = es_client_manager.get_client()
    return MetaIndexService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        column_repo=ColumnESRepo(client=client),
        metric_repo=MetricESRepo(client=client),
        embedding_client=embedding_client_manager.get_client(),
        value_repo=ValueESRepo(client=client),
    )


def build_meta_import_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
) -> MetaImportService:
    """创建元数据批量导入服务。"""
    return MetaImportService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        meta_index_service=build_meta_index_service(meta_repo, source_repo),
        asset_invalidator=build_query_experience_invalidation_service(
            meta_repo.session
        ),
        semantic_index_scheduler=CeleryMetadataSemanticIndexScheduler(),
    )


def build_meta_catalog_service(
    meta_repo: MetaPGRepo,
    source_repo: SourceDorisRepo,
) -> MetaCatalogService:
    """创建元数据目录管理服务。"""
    return MetaCatalogService(
        meta_repo=meta_repo,
        source_repo=source_repo,
        meta_index_service=build_meta_index_service(meta_repo, source_repo),
        asset_invalidator=build_query_experience_invalidation_service(
            meta_repo.session
        ),
        semantic_index_scheduler=CeleryMetadataSemanticIndexScheduler(),
    )
