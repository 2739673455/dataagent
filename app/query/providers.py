"""查询经验索引任务的依赖组装。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from elasticsearch import AsyncElasticsearch
from sqlalchemy.ext.asyncio import AsyncSession

from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.experience_indexer import QueryExperienceIndexer

if TYPE_CHECKING:
    from app.shared.clients.embedding_client import EmbeddingClient


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
