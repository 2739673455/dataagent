"""读取用户的 Doris 查询凭据和资产权限。"""

from app.identity import errors
from app.identity.models.authorization import AssetAccessPolicy
from app.identity.models.doris import DorisQueryIdentity, ResolvedQueryPrincipal
from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.credential import DorisCredentialCipher


class IdentityService:
    """按用户读取查询身份，提供查询凭据和资产策略。"""

    def __init__(self, repo: IdentityPGRepo) -> None:
        """绑定用户和查询身份仓库。"""
        self._repo = repo

    async def get_query_principal(
        self, user_id: int, cipher: DorisCredentialCipher
    ) -> ResolvedQueryPrincipal:
        """读取查询身份并解密凭据。"""
        identity = await self._get_query_identity(user_id)
        return ResolvedQueryPrincipal(
            role_name=identity.role_name,
            query_user=identity.query_user,
            password=cipher.decrypt(identity.encrypted_password),
        )

    async def get_asset_policy(
        self,
        user_id: int,
        doris_repo: DorisRoleRepository,
        *,
        data_source: str,
        database: str,
        catalog: str = "internal",
    ) -> AssetAccessPolicy:
        """读取查询账号的实时 SELECT 授权，构造资产策略。"""
        identity = await self._get_query_identity(user_id)
        grants = await doris_repo.read_authorization(
            role_name=identity.role_name,
            query_user=identity.query_user,
            data_source=data_source,
            catalog=catalog,
            database=database,
        )
        return AssetAccessPolicy(grants=grants)

    async def _get_query_identity(self, user_id: int) -> DorisQueryIdentity:
        """按用户绑定的角色读取查询身份。"""
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise errors.UserSelectionRequiredError(detail="所选用户不存在，请重新选择")
        identity = await self._repo.get_query_identity(user.doris_role_name)
        if identity is None:
            raise errors.QueryPrincipalNotConfiguredError("角色查询身份不存在")
        return identity
