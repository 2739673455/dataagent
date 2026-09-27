"""指标语义索引访问。"""

from typing import Any

from app.metadata.models.catalog import MetricInfo
from app.metadata.repositories.semantic_index import SemanticIndexRepo
from app.shared.config.app_config import cfg
from app.shared.contracts.search import SearchHit


class MetricESRepo(SemanticIndexRepo):
    """指标全文与向量索引存储。"""

    _index_name = cfg.elasticsearch.metric_index
    _resource_label = "指标语义索引"

    async def delete(self, metric_name: str) -> None:
        """删除指标对应的全部语义索引文档。"""
        await self.delete_by_filter([{"term": {"resource_key": metric_name}}])

    async def search_text_hits(
        self,
        query: str,
        *,
        allowed_metrics: frozenset[str] | None,
        limit: int = 5,
    ) -> list[SearchHit[MetricInfo]]:
        """根据关键词检索指标并保留命中分数。"""
        if allowed_metrics is not None and not allowed_metrics:
            return []
        result = await self.search_text(
            query,
            limit=limit,
            resource_filter=(
                self._metric_filter(allowed_metrics)
                if allowed_metrics is not None
                else None
            ),
        )
        return self.parse_hits(result, lambda payload: MetricInfo(**payload))

    async def search_vector_hits(
        self,
        embedding: list[float],
        *,
        allowed_metrics: frozenset[str] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[MetricInfo]]:
        """根据向量检索指标并保留命中分数。"""
        if allowed_metrics is not None and not allowed_metrics:
            return []
        result = await self.search_vector(
            embedding,
            score_threshold=score_threshold,
            limit=limit,
            resource_filter=(
                self._metric_filter(allowed_metrics)
                if allowed_metrics is not None
                else None
            ),
        )
        return self.parse_hits(result, lambda payload: MetricInfo(**payload))

    @staticmethod
    def _metric_filter(allowed_metrics: frozenset[str]) -> dict[str, Any]:
        """构造指标名称白名单过滤条件。"""
        return {"terms": {"resource_key": sorted(allowed_metrics)}}
