"""按用户唯一 Doris 角色解析稳定共享查询身份。"""

from dataclasses import dataclass, field

from app.identity import errors as auth_error
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.credential import DorisCredentialCipher


class QueryPrincipalNotConfiguredError(RuntimeError):
    """用户没有可用的稳定查询身份。"""


@dataclass(frozen=True, slots=True)
class ResolvedQueryPrincipal:
    """服务端为一次查询解析出的 Doris 身份。"""

    role_name: str
    query_user: str
    password: str = field(repr=False)


class QueryPrincipalService:
    """根据用户当前角色解析 Doris 查询身份。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        cipher: DorisCredentialCipher,
    ) -> None:
        """绑定身份存储和查询凭据解密器。"""
        self._repo = repo
        self._cipher = cipher

    async def resolve(self, user_id: int) -> ResolvedQueryPrincipal:
        """一次读取用户和角色身份，获取查询凭据。"""
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        if user.doris_role_name is None:
            raise QueryPrincipalNotConfiguredError("用户尚未配置 Doris 角色")
        identity = await self._repo.get_query_identity(user.doris_role_name)
        if identity is None:
            raise QueryPrincipalNotConfiguredError("角色查询身份不存在")
        principal = ResolvedQueryPrincipal(
            role_name=identity.role_name,
            query_user=identity.query_user,
            password=self._cipher.decrypt(identity.encrypted_password),
        )
        return principal
