"""RBAC 与数据资产白名单授权服务"""

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from app.conf.app_config import DorisRoleConfig
from app.entities.auth import (
    AssetScope,
    DorisRoleAssetGrant,
    User,
    normalize_doris_role_name,
)
from app.errors import auth_error
from app.repositories.auth_pg_repo import AuthPGRepo

T = TypeVar("T")


@dataclass(frozen=True)
class AssetIdentity:
    """层级化数据资产标识"""

    data_source: str
    database_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None

    def __post_init__(self) -> None:
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
            raise ValueError("asset identifiers must be non-empty and trimmed")
        if not self.data_source:
            raise ValueError("data_source is required")
        if self.column_name is not None and self.table_name is None:
            raise ValueError("column_name requires table_name")
        if self.table_name is not None and self.database_name is None:
            raise ValueError("table_name requires database_name")

    @property
    def scope(self) -> AssetScope:
        """返回资产层级"""
        if self.column_name is not None:
            return AssetScope.COLUMN
        if self.table_name is not None:
            return AssetScope.TABLE
        if self.database_name is not None:
            return AssetScope.DATABASE
        return AssetScope.DATA_SOURCE

    @property
    def resource_key(self) -> str:
        """返回无歧义的持久化资源键"""
        canonical = json.dumps(
            [self.data_source, self.database_name, self.table_name, self.column_name],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def encompasses(self, other: "AssetIdentity") -> bool:
        """判断当前授权是否覆盖目标资产"""
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

    def as_dict(self) -> dict[str, str | None]:
        """转换为错误响应可用的结构"""
        return {
            "scope": self.scope.value,
            "data_source": self.data_source,
            "database_name": self.database_name,
            "table_name": self.table_name,
            "column_name": self.column_name,
        }


@dataclass(frozen=True)
class AssetAccessPolicy:
    """用户资产访问策略快照"""

    user_id: int
    grants: frozenset[AssetIdentity] = frozenset()
    unrestricted: bool = False

    def allows(self, asset: AssetIdentity) -> bool:
        """判断是否拥有目标资产的完整访问权"""
        return self.unrestricted or any(
            grant.encompasses(asset) for grant in self.grants
        )

    def is_visible(self, asset: AssetIdentity) -> bool:
        """判断资产或其任一下级资产是否可见"""
        return self.allows(asset) or any(
            asset.encompasses(grant) for grant in self.grants
        )

    def require(self, asset: AssetIdentity) -> None:
        """要求目标资产具备完整访问权"""
        if not self.allows(asset):
            raise auth_error.AssetAccessDeniedError(
                detail="Asset is outside the user's whitelist",
                extensions={"asset": asset.as_dict()},
            )

    def require_all(self, assets: Iterable[AssetIdentity]) -> None:
        """要求全部目标资产具备完整访问权"""
        for asset in assets:
            self.require(asset)

    def filter_allowed(
        self,
        items: Iterable[T],
        identity: Callable[[T], AssetIdentity],
    ) -> list[T]:
        """过滤出具备完整访问权的对象"""
        return [item for item in items if self.allows(identity(item))]

    def filter_visible(
        self,
        items: Iterable[T],
        identity: Callable[[T], AssetIdentity],
    ) -> list[T]:
        """过滤出可用于目录检索展示的对象"""
        return [item for item in items if self.is_visible(identity(item))]


class AuthorizationService:
    """为检索与 SQL 守卫提供用户授权策略"""

    def __init__(self, repo: AuthPGRepo) -> None:
        self._repo = repo

    async def get_asset_policy(self, user_id: int) -> AssetAccessPolicy:
        """构建用户当前资产访问策略"""
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        grants = await self._repo.list_role_asset_grants(user.doris_role_name)
        return AssetAccessPolicy(
            user_id=user.id,
            grants=frozenset(self._grant_identity(grant) for grant in grants),
        )

    async def require_asset_access(
        self,
        user_id: int,
        asset: AssetIdentity,
    ) -> None:
        """执行单个资产的前置访问校验"""
        (await self.get_asset_policy(user_id)).require(asset)

    @staticmethod
    def require_admin(user: User) -> None:
        """要求用户是平台管理员"""
        if not user.is_admin:
            raise auth_error.PermissionDeniedError(
                detail="Platform administrator access is required"
            )

    @staticmethod
    def require_analysis_access(
        user: User,
        roles: Mapping[str, DorisRoleConfig],
    ) -> None:
        """要求用户绑定了已配置的 Doris 数据角色"""
        if user.doris_role_name not in roles:
            raise auth_error.PermissionDeniedError(
                detail="The assigned Doris role is not available"
            )

    @staticmethod
    def _grant_identity(grant: DorisRoleAssetGrant) -> AssetIdentity:
        """将持久化授权转换为资产标识"""
        identity = AssetIdentity(
            data_source=grant.data_source,
            database_name=grant.database_name,
            table_name=grant.table_name,
            column_name=grant.column_name,
        )
        if identity.scope.value != grant.scope:
            raise RuntimeError(f"Invalid persisted asset grant: {grant.id}")
        return identity


@dataclass(frozen=True, slots=True)
class DorisRoleDescriptor:
    """可分配的 Doris 数据角色"""

    name: str
    description: str
    is_default: bool
    query_user: str


class DorisRoleManagementService:
    """平台管理员维护用户与 Doris 角色绑定"""

    def __init__(
        self,
        repo: AuthPGRepo,
        roles: Mapping[str, DorisRoleConfig],
    ) -> None:
        self._repo = repo
        self._roles = roles

    async def list_roles(self) -> list[DorisRoleDescriptor]:
        """列出全部可分配的 Doris 数据角色"""
        return [
            DorisRoleDescriptor(
                name=name,
                description=config.description,
                is_default=config.is_default,
                query_user=config.query_user,
            )
            for name, config in sorted(self._roles.items())
        ]

    async def list_users(self, *, limit: int, offset: int) -> list[User]:
        """分页列出用户与角色"""
        return await self._repo.list_users(limit=limit, offset=offset)

    async def set_user_doris_role(
        self,
        user_id: int,
        role_name: str,
    ) -> User:
        """替换用户唯一 Doris 角色并吊销已有刷新令牌"""
        normalized_role = normalize_doris_role_name(role_name)
        if normalized_role not in self._roles:
            raise auth_error.RoleNotFoundError
        now = datetime.now(UTC)
        async with self._repo.transaction():
            await self._repo.lock_security_mutation()
            user = await self._repo.get_user_by_id(user_id)
            if user is None:
                raise auth_error.UserNotFoundError
            await self._repo.set_user_doris_role(user, normalized_role)
            await self._repo.revoke_user_refresh_tokens(user.id, now)
            updated = await self._repo.get_user_by_id(user.id)
            if updated is None:
                raise RuntimeError("Updated user could not be reloaded")
            return updated

    async def set_user_admin(self, user_id: int, is_admin: bool) -> User:
        """设置平台管理员标志并保护最后一位管理员"""
        now = datetime.now(UTC)
        async with self._repo.transaction():
            await self._repo.lock_security_mutation()
            user = await self._repo.get_user_by_id(user_id)
            if user is None:
                raise auth_error.UserNotFoundError
            if user.is_admin and not is_admin and await self._repo.count_admins() <= 1:
                raise auth_error.LastAdministratorError
            await self._repo.set_user_admin(user, is_admin)
            await self._repo.revoke_user_refresh_tokens(user.id, now)
            updated = await self._repo.get_user_by_id(user.id)
            if updated is None:
                raise RuntimeError("Updated user could not be reloaded")
            return updated

    async def list_asset_grants(
        self,
        role_name: str,
    ) -> list[DorisRoleAssetGrant]:
        """列出 Doris 角色的 SELECT 权限投影"""
        normalized_name = normalize_doris_role_name(role_name)
        if normalized_name not in self._roles:
            raise auth_error.RoleNotFoundError
        return await self._repo.list_role_asset_grants(normalized_name)
