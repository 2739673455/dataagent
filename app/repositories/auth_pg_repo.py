"""PostgreSQL 认证身份与 Doris 权限投影访问"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.models.auth import DorisRoleAssetGrant, RefreshToken, User

_SECURITY_MUTATION_LOCK_KEY = 0x444154414147454E


class AuthPGRepo:
    """PostgreSQL 认证身份与 Doris 权限投影存储"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def transaction(self) -> AsyncSessionTransaction:
        """创建事务上下文"""
        return self._session.begin()

    async def lock_security_mutation(self) -> None:
        """串行化用户身份与跨数据库权限变更"""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _SECURITY_MUTATION_LOCK_KEY},
        )

    async def count_admins(self) -> int:
        """统计当前平台管理员数量"""
        return int(
            await self._session.scalar(
                select(func.count(User.id)).where(User.is_admin.is_(True))
            )
            or 0
        )

    async def add_user(self, user: User) -> User:
        """新增用户并分配主键"""
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_user_by_id(self, user_id: int) -> User | None:
        """按主键读取用户"""
        return await self._session.scalar(
            select(User)
            .where(User.id == user_id)
            .execution_options(populate_existing=True)
        )

    async def get_user_by_email(self, email: str) -> User | None:
        """按规范化邮箱读取用户"""
        return await self._session.scalar(select(User).where(User.email == email))

    async def get_user_by_username(self, username: str) -> User | None:
        """按规范化用户名读取用户"""
        return await self._session.scalar(
            select(User).where(User.username == username)
        )

    async def list_users(self, *, limit: int, offset: int) -> list[User]:
        """分页读取用户"""
        result = await self._session.scalars(
            select(User).order_by(User.id).limit(limit).offset(offset)
        )
        return list(result)

    async def set_user_doris_role(self, user: User, role_name: str) -> None:
        """替换用户唯一 Doris 角色"""
        user.doris_role_name = role_name
        await self._session.flush()

    async def set_user_admin(self, user: User, is_admin: bool) -> None:
        """设置平台管理员标志"""
        user.is_admin = is_admin
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
            select(RefreshToken).where(RefreshToken.id == token_id).with_for_update()
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

    async def list_role_asset_grants(
        self,
        role_name: str,
    ) -> list[DorisRoleAssetGrant]:
        """读取指定 Doris 角色的权限投影"""
        result = await self._session.scalars(
            select(DorisRoleAssetGrant)
            .where(DorisRoleAssetGrant.role_name == role_name)
            .order_by(DorisRoleAssetGrant.resource_key)
        )
        return list(result)

    async def find_asset_grant(
        self,
        role_name: str,
        scope: str,
        resource_key: str,
    ) -> DorisRoleAssetGrant | None:
        """按角色和资产键读取权限投影"""
        return await self._session.scalar(
            select(DorisRoleAssetGrant).where(
                DorisRoleAssetGrant.role_name == role_name,
                DorisRoleAssetGrant.scope == scope,
                DorisRoleAssetGrant.resource_key == resource_key,
            )
        )

    async def add_asset_grant(
        self,
        grant: DorisRoleAssetGrant,
    ) -> DorisRoleAssetGrant:
        """新增 Doris 权限投影"""
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def delete_asset_grant(self, grant: DorisRoleAssetGrant) -> None:
        """删除 Doris 权限投影"""
        await self._session.delete(grant)
        await self._session.flush()

    async def delete_role_asset_grants(self, role_name: str) -> None:
        """删除指定 Doris 角色的全部权限投影"""
        await self._session.execute(
            delete(DorisRoleAssetGrant).where(
                DorisRoleAssetGrant.role_name == role_name
            )
        )
