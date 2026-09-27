"""字段语义索引访问。"""

from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnKey,
)
from app.metadata.repositories.semantic_index import (
    SemanticIndexRepo,
    column_resource_terms_filter,
)
from app.shared.config.app_config import cfg
from app.shared.contracts.search import SearchHit


class ColumnESRepo(SemanticIndexRepo):
    """字段全文与向量索引存储。"""

    _index_name = cfg.elasticsearch.column_index

    async def search_text_hits(
        self,
        query: str,
        *,
        allowed_columns: frozenset[ColumnKey] | None,
        limit: int = 5,
    ) -> list[SearchHit[ColumnInfo]]:
        """根据关键词检索字段并保留命中分数。"""
        if allowed_columns is not None and not allowed_columns:
            return []
        result = await self.search_text(
            query,
            limit=limit,
            resource_filter=(
                column_resource_terms_filter(allowed_columns)
                if allowed_columns is not None
                else None
            ),
        )
        return self.parse_hits(result, lambda payload: ColumnInfo(**payload))

    async def search_vector_hits(
        self,
        embedding: list[float],
        *,
        allowed_columns: frozenset[ColumnKey] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[ColumnInfo]]:
        """根据向量检索字段并保留命中分数。"""
        if allowed_columns is not None and not allowed_columns:
            return []
        result = await self.search_vector(
            embedding,
            score_threshold=score_threshold,
            limit=limit,
            resource_filter=(
                column_resource_terms_filter(allowed_columns)
                if allowed_columns is not None
                else None
            ),
        )
        return self.parse_hits(result, lambda payload: ColumnInfo(**payload))
