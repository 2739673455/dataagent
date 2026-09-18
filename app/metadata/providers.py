"""元数据应用服务组装。"""

from elasticsearch import AsyncElasticsearch

from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.index import MetaIndexService
from app.shared.clients.embedding_client_manager import EmbeddingClient


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
