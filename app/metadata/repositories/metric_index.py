"""指标语义索引访问。"""

from typing import Any

from app.metadata.models.catalog import MetricInfo
from app.metadata.repositories.semantic_index import SemanticIndexRepo
from app.shared.config.app_config import cfg


class MetricESRepo(SemanticIndexRepo[MetricInfo, str]):
    """指标全文与向量索引存储。"""

    _index_name = cfg.elasticsearch.metric_index
    _resource_label = "指标语义索引"

    async def delete(self, metric_name: str) -> None:
        """删除指标对应的全部语义索引文档。"""
        await self.delete_by_filter([{"term": {"resource_key": metric_name}}])

    @staticmethod
    def _resource_key(key: str) -> str:
        """指标名称即索引资源键。"""
        return key

    @staticmethod
    def _parse_payload(payload: dict[str, Any]) -> MetricInfo:
        """解析索引载荷。"""
        return MetricInfo(**payload)
