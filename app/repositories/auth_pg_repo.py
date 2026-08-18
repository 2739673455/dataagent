"""PostgreSQL 认证与授权数据访问"""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction
from sqlalchemy.orm import selectinload

from app.entities.auth import (
    BASE_PLATFORM_ROLES,
    PlatformRole,
    RefreshToken,
    Role,
    RoleAssetGrant,
    User,
    UserRole,
)

_ROLE_DESCRIPTIONS = {
    PlatformRole.ADMIN: "平台管理员",
    PlatformRole.ANALYST: "数据分析人员",
    PlatformRole.VIEWER: "只读访问人员",
}
_USER_PROVISIONING_LOCK_KEY = 0x444154414147454E


class AuthPGRepo:
    """PostgreSQL 认证与授权存储"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def transaction(self) -> AsyncSessionTransaction:
        """创建事务上下文"""
        return self._session.begin()

    async def lock_user_provisioning(self) -> None:
        """串行化用户创建与高权限授予"""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _USER_PROVISIONING_LOCK_KEY},
        )

    async def ensure_base_roles(self) -> None:
        """幂等创建平台内置角色"""
        statement = insert(Role).values(
            [
                {
                    "name": role.value,
                    "description": _ROLE_DESCRIPTIONS[role],
                }
                for role in BASE_PLATFORM_ROLES
            ]
        )
        await self._session.execute(
            statement.on_conflict_do_nothing(index_elements=[Role.name])
        )

    async def count_users_with_role(self, role: PlatformRole) -> int:
        """统计拥有指定角色的用户数"""
        return int(
            await self._session.scalar(
                select(func.count(UserRole.user_id)).where(
                    UserRole.role_name == role.value
                )
            )
            or 0
        )

    async def add_user(self, user: User) -> User:
        """新增用户并分配主键"""
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_user_by_id(self, user_id: int) -> User | None:
        """按主键读取用户及角色"""
        return await self._session.scalar(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.roles))
            .execution_options(populate_existing=True)
        )

    async def get_user_by_email(self, email: str) -> User | None:
        """按规范化邮箱读取用户及角色"""
        return await self._session.scalar(
            select(User)
            .where(User.email == email)
            .options(selectinload(User.roles))
        )

    async def get_user_by_username(self, username: str) -> User | None:
        """按规范化用户名读取用户及角色"""
        return await self._session.scalar(
            select(User)
            .where(User.username == username)
            .options(selectinload(User.roles))
        )

    async def list_users(self, *, limit: int, offset: int) -> list[User]:
        """分页读取用户及角色"""
        result = await self._session.scalars(
            select(User)
            .options(selectinload(User.roles))
            .order_by(User.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.unique())

    async def list_roles(self) -> list[Role]:
        """读取全部平台角色"""
        result = await self._session.scalars(select(Role).order_by(Role.name))
        return list(result)

    async def get_role(self, role: PlatformRole) -> Role | None:
        """读取指定平台角色"""
        return await self._session.get(Role, role.value)

    async def set_user_roles(
        self,
        user_id: int,
        roles: Collection[PlatformRole],
    ) -> None:
        """整体替换用户角色"""
        await self._session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        self._session.add_all(
            [
                UserRole(user_id=user_id, role_name=role.value)
                for role in sorted(roles, key=str)
            ]
        )
        await self._session.flush()

    async def add_refresh_token(self, token: RefreshToken) -> None:
        """保存刷新令牌"""
        self._session.add(token)
        await self._session.flush()

    async def get_refresh_token_for_update(
        self,
        token_id: UUID,
    ) -> RefreshToken | None:
        """锁定并读取刷新令牌"""
        return await self._session.scalar(
            select(RefreshToken)
            .where(RefreshToken.id == token_id)
            .with_for_update()
        )

    @staticmethod
    def rotate_refresh_token(
        current: RefreshToken,
        replacement_id: UUID,
        revoked_at: datetime,
    ) -> None:
        """标记刷新令牌已轮换"""
        current.revoked_at = revoked_at
        current.replaced_by_id = replacement_id

    @staticmethod
    def revoke_refresh_token(token: RefreshToken, revoked_at: datetime) -> None:
        """吊销单个刷新令牌"""
        if token.revoked_at is None:
            token.revoked_at = revoked_at

    async def revoke_refresh_family(
        self,
        family_id: UUID,
        revoked_at: datetime,
    ) -> None:
        """吊销令牌族中全部有效令牌"""
        await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    async def revoke_user_refresh_tokens(
        self,
        user_id: int,
        revoked_at: datetime,
    ) -> None:
        """吊销用户的全部有效刷新令牌"""
        await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    async def list_asset_grants_for_roles(
        self,
        roles: Collection[PlatformRole],
    ) -> list[RoleAssetGrant]:
        """读取多个角色的全部资产授权"""
        if not roles:
            return []
        result = await self._session.scalars(
            select(RoleAssetGrant)
            .where(RoleAssetGrant.role_name.in_([role.value for role in roles]))
            .order_by(RoleAssetGrant.role_name, RoleAssetGrant.resource_key)
        )
        return list(result)

    async def list_role_asset_grants(
        self,
        role: PlatformRole,
    ) -> list[RoleAssetGrant]:
        """读取指定角色的资产授权"""
        result = await self._session.scalars(
            select(RoleAssetGrant)
            .where(RoleAssetGrant.role_name == role.value)
            .order_by(RoleAssetGrant.resource_key)
        )
        return list(result)

    async def get_asset_grant(self, grant_id: UUID) -> RoleAssetGrant | None:
        """读取资产授权"""
        return await self._session.get(RoleAssetGrant, grant_id)

    async def find_asset_grant(
        self,
        role: PlatformRole,
        scope: str,
        resource_key: str,
    ) -> RoleAssetGrant | None:
        """按角色和资产键读取授权"""
        return await self._session.scalar(
            select(RoleAssetGrant).where(
                RoleAssetGrant.role_name == role.value,
                RoleAssetGrant.scope == scope,
                RoleAssetGrant.resource_key == resource_key,
            )
        )

    async def add_asset_grant(self, grant: RoleAssetGrant) -> RoleAssetGrant:
        """新增角色资产授权"""
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def delete_asset_grant(self, grant: RoleAssetGrant) -> None:
        """删除角色资产授权"""
        await self._session.delete(grant)
        await self._session.flush()

    async def roles_exist(self, roles: Sequence[PlatformRole]) -> bool:
        """检查指定角色是否全部存在"""
        if not roles:
            return True
        count = await self._session.scalar(
            select(func.count(Role.name)).where(
                Role.name.in_([role.value for role in roles])
            )
        )
        return int(count or 0) == len(set(roles))
