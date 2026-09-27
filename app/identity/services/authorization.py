"""读取 Doris 实际授权，为检索提供数据资产白名单。"""

from dataclasses import dataclass

from app.identity import errors as auth_error
from app.identity.models.doris import (
    DorisAuthorizationSnapshot,
    DorisQueryIdentity,
)
from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.identity import IdentityPGRepo


@dataclass(frozen=True)
class AssetIdentity:
    """按数据源、库、表、字段标识资产；授权中为 None 的层级不限制范围。"""

    data_source: str
    database_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None

    def encompasses(self, other: "AssetIdentity") -> bool:
        """判断当前授权是否覆盖目标资产。"""
        own_parts = (
            self.data_source,
            self.database_name,
            self.table_name,
            self.column_name,
        )
        other_parts = (
            other.data_source,
            other.database_name,
            other.table_name,
            other.column_name,
        )
        return all(
            own is None or own == target
            for own, target in zip(own_parts, other_parts, strict=True)
        )


@dataclass(frozen=True)
class AssetAccessPolicy:
    """用户资产访问策略快照。"""

    user_id: int
    role_name: str | None = None
    authorization_fingerprint: str | None = None
    grants: frozenset[AssetIdentity] = frozenset()

    def allows(self, asset: AssetIdentity) -> bool:
        """判断是否拥有目标资产的完整访问权。"""
        return any(grant.encompasses(asset) for grant in self.grants)

    def is_visible(self, asset: AssetIdentity) -> bool:
        """判断资产或其任一下级资产是否可见。"""
        return self.allows(asset) or any(
            asset.encompasses(grant) for grant in self.grants
        )


class AuthorizationService:
    """读取 Doris 实际权限，为元数据召回提供资产白名单。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        doris_repo: DorisRoleRepository,
        *,
        data_source: str,
        database: str,
        catalog: str = "internal",
    ) -> None:
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
        return await self.get_role_asset_policy(user.id, user.doris_role_name)

    async def get_role_asset_policy(
        self,
        user_id: int,
        role_name: str,
    ) -> AssetAccessPolicy:
        """在调用方事务内读取角色授权，构造指定用户的资产访问策略。"""
        identity, snapshot = await self.observe_role(role_name)
        return self.policy_from_snapshot(user_id, identity, snapshot)

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
