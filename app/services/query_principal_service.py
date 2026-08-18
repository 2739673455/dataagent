"""按用户唯一 Doris 角色选择稳定共享查询身份"""

from dataclasses import dataclass
from typing import Protocol

from app.conf.app_config import DorisRoleConfig
from app.entities.auth import User
from app.errors import auth_error


class QueryPrincipalUserProvider(Protocol):
    """查询身份解析所需的用户读取接口"""

    async def get_user_by_id(self, user_id: int) -> User | None: ...


class QueryPrincipalNotConfiguredError(RuntimeError):
    """用户没有可用的稳定查询身份"""


@dataclass(frozen=True, slots=True)
class ResolvedQueryPrincipal:
    """服务端选择的稳定查询身份"""

    role_name: str
    config: DorisRoleConfig


class QueryPrincipalService:
    """根据用户当前角色解析查询身份"""

    def __init__(
        self,
        user_provider: QueryPrincipalUserProvider,
        roles: dict[str, DorisRoleConfig],
    ) -> None:
        self._user_provider = user_provider
        self._roles = roles

    async def resolve(self, user_id: int) -> ResolvedQueryPrincipal:
        """选择用户唯一 Doris 角色对应的查询身份"""
        user = await self._user_provider.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        role = self._roles.get(user.doris_role_name)
        if role is None:
            raise QueryPrincipalNotConfiguredError(
                "The user's Doris role has no query identity"
            )
        return ResolvedQueryPrincipal(
            role_name=user.doris_role_name,
            config=role,
        )
