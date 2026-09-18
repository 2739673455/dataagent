"""字段语义索引访问。"""

from elasticsearch import AsyncElasticsearch

from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnKey,
    column_resource_key,
)
from app.metadata.models.search import (
    SemanticIndexDelta,
    SemanticIndexDocument,
)
from app.metadata.repositories.semantic_index import (
    SemanticIndexRepo,
    column_resource_terms_filter,
    semantic_index_mappings,
)
from app.shared.config.app_config import cfg
from app.shared.contracts.search import SearchHit


class ColumnESRepo:
    """字段全文与向量索引存储。"""

    _index_name = cfg.elasticsearch.column_index
    _index_mappings = semantic_index_mappings(
        {"t_name": {"type": "keyword"}},
    )

    def __init__(self, client: AsyncElasticsearch) -> None:
        """初始化字段语义索引存储。"""
        self._repo = SemanticIndexRepo(
            client,
            index_name=self._index_name,
            resource_label="字段语义索引",
            mappings=self._index_mappings,
        )

    async def ensure_index(self) -> None:
        """确保字段语义索引存在。"""
        await self._repo.ensure_index()

    async def list_resource_documents(
        self,
        resource_key: str,
    ) -> list[SemanticIndexDocument]:
        """读取字段当前语义索引文档。"""
        return await self._repo.list_resource_documents(resource_key)

    async def apply_delta(self, delta: SemanticIndexDelta) -> None:
        """应用字段语义索引差量。"""
        await self._repo.apply_delta(delta)

    async def delete(self, t_name: str, c_name: str) -> None:
        """删除字段对应的全部语义索引文档。"""
        await self._repo.delete_by_filter(
            [{"term": {"resource_key": column_resource_key(t_name, c_name)}}]
        )

    async def search_vector_hits(
        self,
        embedding: list[float],
        *,
        allowed_columns: frozenset[ColumnKey] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[ColumnInfo]]:
        """根据向量检索字段并保留命中分数。"""
        result = await self._repo.search_vector(
            embedding,
            score_threshold=score_threshold,
            limit=limit,
            resource_filter=(
                column_resource_terms_filter(allowed_columns)
                if allowed_columns is not None
                else None
            ),
        )
        return self._repo.parse_hits(result, lambda payload: ColumnInfo(**payload))

    async def search_text_hits(
        self,
        query: str,
        *,
        allowed_columns: frozenset[ColumnKey] | None,
        limit: int = 5,
    ) -> list[SearchHit[ColumnInfo]]:
        """根据关键词检索字段并保留命中分数。"""
        result = await self._repo.search_text(
            query,
            limit=limit,
            resource_filter=(
                column_resource_terms_filter(allowed_columns)
                if allowed_columns is not None
                else None
            ),
        )
        return self._repo.parse_hits(result, lambda payload: ColumnInfo(**payload))
