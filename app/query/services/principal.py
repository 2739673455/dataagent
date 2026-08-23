"""按用户唯一 Doris 角色选择稳定共享查询身份"""

from dataclasses import dataclass, field
from typing import Protocol

from app.identity import errors as auth_error
from app.identity.models import DorisQueryIdentity, User
from app.identity.services.credential import DorisCredentialCipher


class QueryPrincipalUserProvider(Protocol):
    """查询身份解析所需的用户读取接口"""

    async def get_user_by_id(self, user_id: int) -> User | None: ...


class QueryIdentityProvider(Protocol):
    """查询身份解析所需的角色读取接口"""

    async def get(self, role_name: str) -> DorisQueryIdentity | None: ...


class QueryPrincipalNotConfiguredError(RuntimeError):
    """用户没有可用的稳定查询身份"""


@dataclass(frozen=True, slots=True)
class ResolvedQueryPrincipal:
    """服务端选择的稳定查询身份"""

    role_name: str
    query_user: str
    password: str = field(repr=False)
    workload_group: str


class QueryPrincipalService:
    """根据用户当前角色解析查询身份"""

    def __init__(
        self,
        user_provider: QueryPrincipalUserProvider,
        identity_provider: QueryIdentityProvider,
        cipher: DorisCredentialCipher,
    ) -> None:
        self._user_provider = user_provider
        self._identity_provider = identity_provider
        self._cipher = cipher

    async def resolve(self, user_id: int) -> ResolvedQueryPrincipal:
        """选择用户唯一 Doris 角色对应的查询身份"""
        user = await self._user_provider.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        if user.doris_role_name is None:
            raise QueryPrincipalNotConfiguredError(
                "The user has no Doris role"
            )
        identity = await self._identity_provider.get(user.doris_role_name)
        if identity is None or not identity.is_active:
            raise QueryPrincipalNotConfiguredError(
                "The user's Doris role has no query identity"
            )
        return ResolvedQueryPrincipal(
            role_name=user.doris_role_name,
            query_user=identity.query_user,
            password=self._cipher.decrypt(identity.encrypted_password),
            workload_group=identity.workload_group,
        )
