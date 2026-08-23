"""Doris 查询身份 PostgreSQL 访问"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import DorisQueryIdentity, User


class DorisQueryIdentityPGRepo:
    """持久化 Doris 角色与稳定查询身份"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """返回当前存储绑定的数据库会话"""
        return self._session

    async def add(self, identity: DorisQueryIdentity) -> DorisQueryIdentity:
        """新增查询身份"""
        self._session.add(identity)
        await self._session.flush()
        return identity

    async def get(self, role_name: str) -> DorisQueryIdentity | None:
        """按 Doris 角色读取查询身份"""
        return await self._session.scalar(
            select(DorisQueryIdentity).where(
                DorisQueryIdentity.role_name == role_name
            )
        )

    async def get_by_query_user(self, query_user: str) -> DorisQueryIdentity | None:
        """按 Doris 用户读取查询身份"""
        return await self._session.scalar(
            select(DorisQueryIdentity).where(
                DorisQueryIdentity.query_user == query_user
            )
        )

    async def get_default(self) -> DorisQueryIdentity | None:
        """读取当前缺省查询身份"""
        return await self._session.scalar(
            select(DorisQueryIdentity).where(
                DorisQueryIdentity.is_default.is_(True),
                DorisQueryIdentity.is_active.is_(True),
            )
        )

    async def list_all(self) -> list[DorisQueryIdentity]:
        """列出全部查询身份"""
        result = await self._session.scalars(
            select(DorisQueryIdentity).order_by(DorisQueryIdentity.role_name)
        )
        return list(result)

    async def list_active(self) -> list[DorisQueryIdentity]:
        """列出全部启用的查询身份"""
        result = await self._session.scalars(
            select(DorisQueryIdentity)
            .where(DorisQueryIdentity.is_active.is_(True))
            .order_by(DorisQueryIdentity.role_name)
        )
        return list(result)

    async def clear_default(self) -> None:
        """清除当前缺省标记"""
        await self._session.execute(
            update(DorisQueryIdentity)
            .where(DorisQueryIdentity.is_default.is_(True))
            .values(is_default=False)
        )

    async def count_assigned_users(self, role_name: str) -> int:
        """统计绑定指定角色的平台用户"""
        return int(
            await self._session.scalar(
                select(func.count(User.id)).where(User.doris_role_name == role_name)
            )
            or 0
        )

    async def delete(self, identity: DorisQueryIdentity) -> None:
        """删除查询身份"""
        await self._session.delete(identity)
        await self._session.flush()

    async def flush(self) -> None:
        """持久化当前查询身份变更"""
        await self._session.flush()
