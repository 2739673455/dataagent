"""字段语义索引访问。"""

from typing import Any

from app.metadata.contracts import ColumnKey
from app.metadata.models.catalog import ColumnInfo, column_resource_key
from app.metadata.repositories.semantic_index import (
    SemanticIndexRepo,
)
from app.shared.config.app_config import cfg


class ColumnESRepo(SemanticIndexRepo[ColumnInfo, ColumnKey]):
    """字段全文与向量索引存储。"""

    _index_name = cfg.elasticsearch.column_index
    _resource_label = "字段语义索引"

    async def delete(self, t_name: str, c_name: str) -> None:
        """删除字段对应的全部语义索引文档。"""
        await self.delete_by_filter(
            [{"term": {"resource_key": column_resource_key(t_name, c_name)}}]
        )

    @staticmethod
    def _resource_key(key: ColumnKey) -> str:
        """构造字段资源键。"""
        return column_resource_key(*key)

    @staticmethod
    def _parse_payload(payload: dict[str, Any]) -> ColumnInfo:
        """解析索引载荷。"""
        return ColumnInfo(**payload)
