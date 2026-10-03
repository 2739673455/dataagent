"""RBAC 与数据资产白名单授权服务。"""

from __future__ import annotations

from app.identity import errors as auth_error
from app.identity.contracts import AssetAccessPolicy, AssetIdentity
from app.identity.models.doris import (
    DorisAuthorizationSnapshot,
    DorisQueryIdentity,
)
from app.identity.repositories.doris_role import (
    DorisRoleRepository,
)
from app.identity.repositories.identity import IdentityPGRepo


class AuthorizationService:
    """读取 Doris 当前授权，构造资产策略并更新权限指纹。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        doris_repo: DorisRoleRepository,
        *,
        data_source: str,
        database: str,
        catalog: str = "internal",
    ) -> None:
        """绑定身份与 Doris 角色仓储，以及授权策略对应的数据范围。"""
        self._repo = repo
        self._doris_repo = doris_repo
        self._data_source = data_source
        self._database = database
        self._catalog = catalog

    async def get_asset_policy(self, user_id: int) -> AssetAccessPolicy:
        """在调用方事务内读取身份、Doris 当前授权并更新权限指纹。"""
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        if user.doris_role_name is None:
            return AssetAccessPolicy(user_id=user.id)
        return await self.get_role_asset_policy(user.id, user.doris_role_name)

    async def observe_role(
        self,
        role_name: str,
    ) -> tuple[DorisQueryIdentity, DorisAuthorizationSnapshot]:
        """先锁身份再读取 Doris，避免较早读取的快照覆盖较新的权限指纹。"""
        identity = await self._repo.lock_query_identity(role_name)
        if identity is None:
            raise auth_error.RoleNotFoundError
        snapshot = await self._doris_repo.read_authorization(
            role_name=identity.role_name,
            query_user=identity.query_user,
            data_source=self._data_source,
            catalog=self._catalog,
            database=self._database,
        )
        if identity.authorization_fingerprint != snapshot.fingerprint:
            identity.authorization_fingerprint = snapshot.fingerprint
            await self._repo.flush()
        return identity, snapshot

    async def get_role_asset_policy(
        self,
        user_id: int,
        role_name: str,
    ) -> AssetAccessPolicy:
        """在调用方事务内观察角色当前授权，并构造指定用户的资产访问策略。"""
        identity, snapshot = await self.observe_role(role_name)
        return self.policy_from_snapshot(user_id, identity, snapshot)

    @staticmethod
    def policy_from_snapshot(
        user_id: int,
        identity: DorisQueryIdentity,
        snapshot: DorisAuthorizationSnapshot,
    ) -> AssetAccessPolicy:
        """将同一次观察得到的授权内容构造成不可变策略。"""
        return AssetAccessPolicy(
            user_id=user_id,
            role_name=identity.role_name,
            authorization_fingerprint=snapshot.fingerprint,
            grants=frozenset(
                AssetIdentity(
                    grant.data_source,
                    grant.database_name,
                    grant.table_name,
                    grant.column_name,
                )
                for grant in snapshot.grants
            ),
        )
