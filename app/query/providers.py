"""查询应用服务依赖组装。"""

from elasticsearch import AsyncElasticsearch
from sqlalchemy.ext.asyncio import AsyncSession

from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.runtime import DatabaseQueryExecutionRuntime
from app.query.services.contracts import QueryExperienceIndexScheduler
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.execution_recorder import QueryExecutionRecorder
from app.query.services.executor import QueryArtifactStore
from app.query.services.experience_indexer import QueryExperienceIndexer
from app.query.services.experience_invalidation import (
    QueryExperienceInvalidationService,
)
from app.query.services.experience_recall import QueryExperienceRecallService
from app.query.task_scheduler import query_experience_index_scheduler
from app.shared.clients.embedding_client_manager import (
    EmbeddingClient,
    embedding_client_manager,
)
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.config.app_config import cfg


def build_query_execution_recorder(
    session: AsyncSession,
    *,
    index_scheduler: QueryExperienceIndexScheduler = query_experience_index_scheduler,
) -> QueryExecutionRecorder:
    """创建查询执行记录与经验聚合服务。"""
    return QueryExecutionRecorder(
        execution_repo=QueryExecutionPGRepo(session),
        experience_repo=QueryExperiencePGRepo(session),
        index_scheduler=index_scheduler,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


def build_query_experience_recall_service(
    session: AsyncSession,
    *,
    index_scheduler: QueryExperienceIndexScheduler = query_experience_index_scheduler,
) -> QueryExperienceRecallService:
    """创建查询经验混合召回服务。"""
    return QueryExperienceRecallService(
        repo=QueryExperiencePGRepo(session),
        index_repo=QueryExperienceESRepo(client=es_client_manager.get_client()),
        embedding_client=embedding_client_manager.get_client(),
        index_scheduler=index_scheduler,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


def build_query_experience_indexer(
    session: AsyncSession,
    es_client: AsyncElasticsearch,
    embedding_client: EmbeddingClient,
) -> QueryExperienceIndexer:
    """创建查询经验索引同步服务。"""
    return QueryExperienceIndexer(
        repo=QueryExperiencePGRepo(session),
        index_repo=QueryExperienceESRepo(es_client),
        embedding_client=embedding_client,
    )


def build_query_experience_invalidation_service(
    session: AsyncSession,
    *,
    index_scheduler: QueryExperienceIndexScheduler = query_experience_index_scheduler,
) -> QueryExperienceInvalidationService:
    """创建不依赖 Elasticsearch 和 Embedding 的查询经验失效服务。"""
    return QueryExperienceInvalidationService(
        repo=QueryExperiencePGRepo(session=session),
        index_scheduler=index_scheduler,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


def build_query_execution_handler(
    artifact_store: QueryArtifactStore,
) -> QueryExecutionHandler:
    """组装身份解析、受控执行和历史记录完整查询用例。"""
    return QueryExecutionHandler(
        DatabaseQueryExecutionRuntime(artifact_store, build_query_execution_recorder)
    )
