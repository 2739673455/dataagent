"""元数据应用服务组装。"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from elasticsearch import AsyncElasticsearch

from app.identity.services.authorization import AssetAccessPolicy
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.index import MetaIndexService
from app.metadata.services.recall import SemanticRecallContextService
from app.metadata.services.search import SemanticCatalog, SemanticResourceRecallService
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg


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


async def build_semantic_resource_recall_service(
    postgres: PostgresClientManager,
    es_client: AsyncElasticsearch,
    embedding_client: EmbeddingClient,
    policy: AssetAccessPolicy,
) -> SemanticResourceRecallService:
    """先读取完整目录并关闭会话，再构建不依赖数据库事务的检索服务。"""
    async with postgres.session() as session:
        repo = MetaPGRepo(session)
        tables = await repo.list_table_infos()
        columns = await repo.list_column_infos()
        metrics = await repo.list_metric_infos()
    return SemanticResourceRecallService(
        embedding_client=embedding_client,
        column_repo=ColumnESRepo(es_client),
        metric_repo=MetricESRepo(es_client),
        value_repo=ValueESRepo(es_client),
        catalog=SemanticCatalog(
            tables={item.name: item for item in tables},
            columns={(item.t_name, item.name): item for item in columns},
            metrics={item.name: item for item in metrics},
        ),
        asset_policy=policy,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


@asynccontextmanager
async def semantic_recall_context(
    postgres: PostgresClientManager,
    policy: AssetAccessPolicy,
) -> AsyncGenerator[SemanticRecallContextService]:
    """在短事务中组装授权后的快照读写服务。"""
    async with postgres.session() as session, session.begin():
        yield SemanticRecallContextService(
            SemanticRecallPGRepo(session),
            MetadataAuthorizationFilter(
                policy, cfg.query.data_source, cfg.doris.database
            ),
            query_experience_role_name=policy.role_name,
            query_experience_authorization_fingerprint=policy.authorization_fingerprint,
        )
