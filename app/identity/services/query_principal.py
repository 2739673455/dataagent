"""按用户唯一 Doris 角色解析稳定共享查询身份。"""

from dataclasses import dataclass, field

from app.identity import errors as auth_error
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.authorization import AssetAccessPolicy, AuthorizationService
from app.identity.services.credential import DorisCredentialCipher


class QueryPrincipalNotConfiguredError(RuntimeError):
    """用户没有可用的稳定查询身份。"""


@dataclass(frozen=True, slots=True)
class ResolvedQueryPrincipal:
    """服务端为一次查询解析出的 Doris 身份。"""

    role_name: str
    authorization_fingerprint: str
    query_user: str
    workload_group: str
    password: str = field(repr=False)


class QueryPrincipalService:
    """根据用户当前角色解析 Doris 查询身份。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        cipher: DorisCredentialCipher,
        authorization: AuthorizationService,
    ) -> None:
        """绑定身份存储和查询凭据解密器。"""
        self._repo = repo
        self._cipher = cipher
        self._authorization = authorization

    async def resolve(
        self, user_id: int
    ) -> tuple[ResolvedQueryPrincipal, AssetAccessPolicy]:
        """一次读取用户和角色身份，派生查询凭据及资产策略。"""
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        if user.doris_role_name is None:
            raise QueryPrincipalNotConfiguredError("用户尚未配置 Doris 角色")
        try:
            identity, snapshot = await self._authorization.observe_role(
                user.doris_role_name
            )
        except auth_error.RoleNotFoundError as exc:
            raise QueryPrincipalNotConfiguredError("角色查询身份不存在") from exc
        policy = self._authorization.policy_from_snapshot(user.id, identity, snapshot)
        principal = ResolvedQueryPrincipal(
            role_name=identity.role_name,
            authorization_fingerprint=snapshot.fingerprint,
            query_user=identity.query_user,
            password=self._cipher.decrypt(identity.encrypted_password),
            workload_group=identity.workload_group,
        )
        return principal, policy
