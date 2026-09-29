"""字段语义索引访问。"""

from app.metadata.models.catalog import ColumnInfo, ColumnKey
from app.metadata.repositories.semantic_index import (
    SemanticIndexRepo,
    column_resource_terms_filter,
)


class ColumnESRepo(SemanticIndexRepo[ColumnInfo, ColumnKey]):
    """字段全文与向量索引存储。"""

    _index_name = "data-agent-column"
    _payload_type = ColumnInfo
    _resource_filter = staticmethod(column_resource_terms_filter)
