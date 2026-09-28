"""检索使用的数据资产标识与访问策略。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetIdentity:
    """按数据源、库、表、字段标识资产；None 匹配对应层级的全部资产。"""

    data_source: str
    database_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None

    def encompasses(self, other: "AssetIdentity") -> bool:
        """逐层比较资产标识，判断当前范围是否覆盖目标。"""
        own_parts = (
            self.data_source,
            self.database_name,
            self.table_name,
            self.column_name,
        )
        other_parts = (
            other.data_source,
            other.database_name,
            other.table_name,
            other.column_name,
        )
        return all(
            own is None or own == target
            for own, target in zip(own_parts, other_parts, strict=True)
        )


@dataclass(frozen=True)
class AssetAccessPolicy:
    """基于 SELECT 授权判断资产访问范围。"""

    grants: frozenset[AssetIdentity] = frozenset()

    def allows(self, asset: AssetIdentity) -> bool:
        """判断是否拥有目标资产的完整访问权。"""
        return any(grant.encompasses(asset) for grant in self.grants)

    def is_visible(self, asset: AssetIdentity) -> bool:
        """判断资产或其任一下级资产是否可见。"""
        return self.allows(asset) or any(
            asset.encompasses(grant) for grant in self.grants
        )
