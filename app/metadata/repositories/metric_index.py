"""指标语义索引访问。"""

from typing import Any

from elasticsearch import AsyncElasticsearch

from app.metadata.models.catalog import MetricInfo
from app.metadata.models.search import (
    SemanticIndexDelta,
    SemanticIndexDocument,
)
from app.metadata.repositories.semantic_index import (
    SemanticIndexRepo,
    semantic_index_mappings,
)
from app.shared.config.app_config import cfg
from app.shared.contracts.search import SearchHit


class MetricESRepo:
    """指标全文与向量索引存储。"""

    _index_name = cfg.elasticsearch.metric_index
    _index_mappings = semantic_index_mappings()

    def __init__(self, client: AsyncElasticsearch) -> None:
        """初始化指标语义索引存储。"""
        self._repo = SemanticIndexRepo(
            client,
            index_name=self._index_name,
            resource_label="指标语义索引",
            mappings=self._index_mappings,
        )

    async def ensure_index(self) -> None:
        """确保指标语义索引存在。"""
        await self._repo.ensure_index()

    async def list_resource_documents(
        self,
        resource_key: str,
    ) -> list[SemanticIndexDocument]:
        """读取指标当前语义索引文档。"""
        return await self._repo.list_resource_documents(resource_key)

    async def apply_delta(self, delta: SemanticIndexDelta) -> None:
        """应用指标语义索引差量。"""
        await self._repo.apply_delta(delta)

    async def delete(self, metric_name: str) -> None:
        """删除指标对应的全部语义索引文档。"""
        await self._repo.delete_by_filter([{"term": {"resource_key": metric_name}}])

    async def search_vector_hits(
        self,
        embedding: list[float],
        *,
        allowed_metrics: frozenset[str] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[MetricInfo]]:
        """根据向量检索指标并保留命中分数。"""
        result = await self._repo.search_vector(
            embedding,
            score_threshold=score_threshold,
            limit=limit,
            resource_filter=(
                self._metric_filter(allowed_metrics)
                if allowed_metrics is not None
                else None
            ),
        )
        return self._repo.parse_hits(result, lambda payload: MetricInfo(**payload))

    async def search_text_hits(
        self,
        query: str,
        *,
        allowed_metrics: frozenset[str] | None,
        limit: int = 5,
    ) -> list[SearchHit[MetricInfo]]:
        """根据关键词检索指标并保留命中分数。"""
        result = await self._repo.search_text(
            query,
            limit=limit,
            resource_filter=(
                self._metric_filter(allowed_metrics)
                if allowed_metrics is not None
                else None
            ),
        )
        return self._repo.parse_hits(result, lambda payload: MetricInfo(**payload))

    @staticmethod
    def _metric_filter(allowed_metrics: frozenset[str]) -> dict[str, Any]:
        """构造指标名称白名单过滤条件。"""
        if not allowed_metrics:
            raise ValueError("allowed_metrics 列表不能为空")
        return {"terms": {"resource_key": sorted(allowed_metrics)}}
