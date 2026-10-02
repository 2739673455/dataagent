"""身份、资产授权和查询身份的公开值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.shared.contracts.assets import asset_resource_key


class AssetScope(StrEnum):
    """数据资产授权粒度。"""

    DATA_SOURCE = "data_source"
    DATABASE = "database"
    TABLE = "table"
    COLUMN = "column"


@dataclass(frozen=True)
class AssetIdentity:
    """层级化数据资产标识。"""

    data_source: str
    database_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None

    def __post_init__(self) -> None:
        """校验资产层级字段之间的依赖关系。"""
        values = (
            self.data_source,
            self.database_name,
            self.table_name,
            self.column_name,
        )
        if any(
            value is not None and (not value or value != value.strip())
            for value in values
        ):
            raise ValueError("资产标识符不能为空且不能包含前后空白字符")
        if not self.data_source:
            raise ValueError("data_source 不能为空")
        if self.column_name is not None and self.table_name is None:
            raise ValueError("指定 column_name 时必须同时指定 table_name")
        if self.table_name is not None and self.database_name is None:
            raise ValueError("指定 table_name 时必须同时指定 database_name")

    @property
    def scope(self) -> AssetScope:
        """返回资产层级。"""
        if self.column_name is not None:
            return AssetScope.COLUMN
        if self.table_name is not None:
            return AssetScope.TABLE
        if self.database_name is not None:
            return AssetScope.DATABASE
        return AssetScope.DATA_SOURCE

    @property
    def resource_key(self) -> str:
        """返回无歧义的持久化资源键。"""
        return asset_resource_key(
            self.data_source,
            self.database_name,
            self.table_name,
            self.column_name,
        )

    def encompasses(self, other: AssetIdentity) -> bool:
        """判断当前授权是否覆盖目标资产。"""
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
    """用户资产访问策略快照。"""

    user_id: int
    role_name: str | None = None
    authorization_fingerprint: str | None = None
    grants: frozenset[AssetIdentity] = frozenset()

    def allows(self, asset: AssetIdentity) -> bool:
        """判断是否拥有目标资产的完整访问权。"""
        return any(grant.encompasses(asset) for grant in self.grants)

    def is_visible(self, asset: AssetIdentity) -> bool:
        """判断资产或其任一下级资产是否可见。"""
        return self.allows(asset) or any(
            asset.encompasses(grant) for grant in self.grants
        )


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """脱离数据库会话的认证用户快照。"""

    id: int
    username: str
    email: str
    auth_version: int
    is_active: bool
    is_admin: bool
    doris_role_name: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedQueryPrincipal:
    """服务端为一次查询解析出的 Doris 身份。"""

    role_name: str
    authorization_fingerprint: str
    query_user: str
    workload_group: str
    password: str = field(repr=False)
