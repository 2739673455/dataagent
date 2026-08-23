"""元数据应用服务依赖的外部能力端口"""

from typing import Protocol

from app.metadata.models import ColumnKey


class MetadataAssetInvalidator(Protocol):
    """使引用已变更元数据的派生资产失效"""

    async def invalidate_assets(
        self,
        *,
        table_names: set[str],
        column_keys: set[ColumnKey],
    ) -> object:
        """使引用指定表或字段的派生资产失效"""
        ...
