"""按用户唯一 Doris 角色选择稳定共享查询身份"""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.identity import errors as auth_error
from app.identity.models.account import User
from app.identity.models.doris import DorisQueryIdentity


class QueryPrincipalUserProvider(Protocol):
    """查询身份解析所需的用户读取能力"""

    async def get_user_by_id(self, user_id: int) -> User | None:
        """按主键读取查询发起用户"""
        ...


class QueryIdentityProvider(Protocol):
    """查询身份解析所需的角色身份读取能力"""

    async def get(self, role_name: str) -> DorisQueryIdentity | None:
        """读取 Doris 角色对应的查询身份"""
        ...


class QueryCredentialDecryptor(Protocol):
    """查询身份解析所需的凭据解密能力"""

    def decrypt(self, encrypted_password: str) -> str:
        """解密 Doris 查询用户密码"""
        ...


class QueryPrincipalNotConfiguredError(RuntimeError):
    """用户没有可用的稳定查询身份"""


@dataclass(frozen=True, slots=True)
class ResolvedQueryPrincipal:
    """服务端选择的稳定查询身份"""

    role_name: str
    authorization_epoch: UUID
    query_user: str
    workload_group: str
    password: str = field(repr=False)


class QueryPrincipalService:
    """根据用户当前角色解析查询身份"""

    def __init__(
        self,
        user_provider: QueryPrincipalUserProvider,
        identity_provider: QueryIdentityProvider,
        cipher: QueryCredentialDecryptor,
    ) -> None:
        """绑定用户、查询身份和凭据解密依赖"""
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
            raise QueryPrincipalNotConfiguredError("用户尚未配置 Doris 角色")
        identity = await self._identity_provider.get(user.doris_role_name)
        if identity is None:
            raise QueryPrincipalNotConfiguredError(
                "用户的 Doris 角色尚未配置可用的查询身份"
            )
        return ResolvedQueryPrincipal(
            role_name=user.doris_role_name,
            authorization_epoch=identity.authorization_epoch,
            query_user=identity.query_user,
            password=self._cipher.decrypt(identity.encrypted_password),
            workload_group=identity.workload_group,
        )
