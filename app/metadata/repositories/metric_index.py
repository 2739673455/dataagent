"""指标语义索引访问。"""

from typing import Any

from app.metadata.models.catalog import MetricInfo
from app.metadata.repositories.semantic_index import SemanticIndexRepo


class MetricESRepo(SemanticIndexRepo[MetricInfo, str]):
    """指标全文与向量索引存储。"""

    _index_name = "data-agent-metric"
    _payload_type = MetricInfo

    @staticmethod
    def _resource_filter(allowed_keys: frozenset[str]) -> dict[str, Any]:
        """构造指标名称白名单过滤条件。"""
        return {"terms": {"resource_key": sorted(allowed_keys)}}
