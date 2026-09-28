"""预定义用户与 Doris 查询凭据访问。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models.account import User
from app.identity.models.doris import DorisQueryIdentity


class IdentityPGRepo:
    """读取用户及 Doris 查询身份。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定调用方管理的数据库会话，事务由调用方提交。"""
        self._session = session

    async def list_users(self) -> list[User]:
        """按 ID 列出已初始化的预定义用户，供前端选择。"""
        return list(await self._session.scalars(select(User).order_by(User.id)))

    async def get_user_by_id(self, user_id: int) -> User | None:
        """按主键读取用户。"""
        return await self._session.scalar(
            select(User)
            .where(User.id == user_id)
            .execution_options(populate_existing=True)
        )

    async def get_query_identity(
        self,
        role_name: str,
    ) -> DorisQueryIdentity | None:
        """按 Doris 角色读取查询账号和凭据。"""
        return await self._session.scalar(
            select(DorisQueryIdentity).where(DorisQueryIdentity.role_name == role_name)
        )
