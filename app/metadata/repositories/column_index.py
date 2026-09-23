"""字段语义索引访问。"""

from elasticsearch import AsyncElasticsearch

from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnKey,
)
from app.metadata.models.search import (
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

    async def reset_index(self) -> None:
        """重建语义索引。"""
        await self._repo.reset_index()

    async def write_documents(self, documents: list[SemanticIndexDocument]) -> None:
        """写入完整语义索引文档。"""
        await self._repo.write_documents(documents)

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
