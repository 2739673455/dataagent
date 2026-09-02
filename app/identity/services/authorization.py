"""RBAC 与数据资产白名单授权服务。"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.identity import errors as auth_error
from app.identity.models.account import User
from app.identity.models.doris import (
    AssetScope,
    DorisQueryIdentity,
    DorisRoleAssetGrant,
    DorisRowPolicy,
    normalize_doris_role_name,
)
from app.identity.repositories.doris_role import (
    DorisQueryUserAlreadyExistsError,
    DorisRoleAlreadyExistsError,
    DorisRoleIdentityDropError,
    DorisRoleIdentityDropState,
    DorisRoleRepository,
    DorisWorkloadGroupNotFoundError,
    role_name_from_row,
    role_users_from_row,
)
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.account_validation import (
    validate_email,
    validate_password_length,
    validate_username,
)
from app.identity.services.auth import AuthenticatedUser, PasswordManager
from app.identity.services.credential import DorisCredentialCipher
from app.shared.config.app_config import AuthConfig
from app.shared.contracts.assets import asset_resource_key


class QueryClientInvalidator(Protocol):
    """Doris 角色变更所需的查询客户端失效能力。"""

    async def invalidate(self, role_name: str) -> None:
        """关闭并移除指定角色的共享查询客户端。"""
        ...


@dataclass(frozen=True)
class AssetIdentity:
    """层级化数据资产标识。"""

    data_source: str
    database_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None

    def __post_init__(self) -> None:
        """校验资产层级字段之间的依赖关系。"""
        values = (
            self.data_source,
            self.database_name,
            self.table_name,
            self.column_name,
        )
        if any(
            value is not None and (not value or value != value.strip())
            for value in values
        ):
            raise ValueError("资产标识符不能为空且不能包含前后空白字符")
        if not self.data_source:
            raise ValueError("data_source 不能为空")
        if self.column_name is not None and self.table_name is None:
            raise ValueError("指定 column_name 时必须同时指定 table_name")
        if self.table_name is not None and self.database_name is None:
            raise ValueError("指定 table_name 时必须同时指定 database_name")

    @property
    def scope(self) -> AssetScope:
        """返回资产层级。"""
        if self.column_name is not None:
            return AssetScope.COLUMN
        if self.table_name is not None:
            return AssetScope.TABLE
        if self.database_name is not None:
            return AssetScope.DATABASE
        return AssetScope.DATA_SOURCE

    @property
    def resource_key(self) -> str:
        """返回无歧义的持久化资源键。"""
        return asset_resource_key(
            self.data_source,
            self.database_name,
            self.table_name,
            self.column_name,
        )

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
    authorization_epoch: UUID | None = None
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
    """为检索与 SQL 守卫提供用户授权策略。"""

    def __init__(self, repo: IdentityPGRepo) -> None:
        """绑定认证授权投影仓储。"""
        self._repo = repo

    async def get_asset_policy(self, user_id: int) -> AssetAccessPolicy:
        """构建用户当前资产访问策略。"""
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        if user.doris_role_name is None:
            return AssetAccessPolicy(user_id=user.id)
        identity = await self._repo.get_query_identity(user.doris_role_name)
        if identity is None:
            return AssetAccessPolicy(user_id=user.id)
        grants = await self._repo.list_role_asset_grants(user.doris_role_name)
        return AssetAccessPolicy(
            user_id=user.id,
            role_name=user.doris_role_name,
            authorization_epoch=identity.authorization_epoch,
            grants=frozenset(self._grant_identity(grant) for grant in grants),
        )

    @staticmethod
    def require_admin(user: AuthenticatedUser) -> None:
        """要求用户是平台管理员。"""
        if not user.is_admin:
            raise auth_error.PermissionDeniedError(detail="需要平台管理员权限")

    @staticmethod
    def require_analysis_access(
        user: AuthenticatedUser,
        identity: DorisQueryIdentity | None,
    ) -> None:
        """要求用户绑定了 Doris 查询身份。"""
        if user.doris_role_name is None or identity is None:
            raise auth_error.PermissionDeniedError(detail="分配的 Doris 角色不可用")

    @staticmethod
    def _grant_identity(grant: DorisRoleAssetGrant) -> AssetIdentity:
        """将持久化授权转换为资产标识。"""
        identity = AssetIdentity(
            data_source=grant.data_source,
            database_name=grant.database_name,
            table_name=grant.table_name,
            column_name=grant.column_name,
        )
        if identity.scope.value != grant.scope:
            raise RuntimeError(f"持久化资产授权记录无效: {grant.id}")
        return identity


@dataclass(frozen=True, slots=True)
class DorisExistingRoleDescriptor:
    """Doris 中已存在的角色及平台管理状态。"""

    name: str
    managed: bool
    doris_users: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RoleSelectGrantSnapshot:
    """恢复 Doris SELECT 权限所需的单条投影快照。"""

    scope: str
    database_name: str | None
    table_name: str | None
    column_name: str | None


@dataclass(frozen=True, slots=True)
class _RoleDeletionSnapshot:
    """跨存储删除 Doris 角色前保存的完整恢复状态。"""

    role_name: str
    query_user: str
    workload_group: str
    password: str = field(repr=False)
    select_grants: tuple[_RoleSelectGrantSnapshot, ...] = ()
    row_policies: tuple[DorisRowPolicy, ...] = ()


class DorisRoleManagementService:
    """平台管理员维护用户与 Doris 角色绑定。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        doris_repo: DorisRoleRepository,
        cipher: DorisCredentialCipher,
        client_registry: QueryClientInvalidator,
        password_manager: PasswordManager,
        auth_config: AuthConfig,
    ) -> None:
        """初始化 Doris 角色、凭据和用户绑定管理依赖。"""
        self._repo = repo
        self._doris_repo = doris_repo
        self._cipher = cipher
        self._client_registry = client_registry
        self._password_manager = password_manager
        self._auth_config = auth_config

    async def list_workload_groups(self) -> tuple[str, ...]:
        """列出创建角色时可选择的 Doris 工作组。"""
        return await self._doris_repo.list_workload_groups()

    async def list_existing_roles(self) -> list[DorisExistingRoleDescriptor]:
        """列出 Doris 原生角色并标记平台管理状态。"""
        rows = await self._doris_repo.list_roles()
        managed_names = {
            identity.role_name for identity in await self._repo.list_query_identities()
        }
        roles = [
            DorisExistingRoleDescriptor(
                name=role_name,
                managed=role_name in managed_names,
                doris_users=role_users_from_row(row),
            )
            for row in rows
            if (role_name := role_name_from_row(row)) is not None
        ]
        return sorted(roles, key=lambda role: role.name.casefold())

    async def create_role(
        self,
        *,
        role_name: str,
        description: str,
        query_user: str,
        workload_group: str,
    ) -> DorisQueryIdentity:
        """创建 Doris 角色及唯一稳定查询身份。"""
        role = normalize_doris_role_name(role_name)
        self._doris_repo.quote_identifier(query_user)
        self._doris_repo.quote_identifier(workload_group)
        await self._require_workload_group(workload_group)
        password = self._cipher.generate_password()
        doris_created = False
        try:
            async with self._repo.session.begin():
                await self._repo.lock_security_mutation()
                if await self._repo.get_query_identity(role) is not None:
                    raise auth_error.RoleAlreadyExistsError
                if (
                    await self._repo.get_query_identity_by_query_user(query_user)
                    is not None
                ):
                    raise auth_error.QueryUserAlreadyExistsError(
                        detail=f"Doris 查询用户 {query_user} 已存在"
                    )
                await self._doris_repo.create_role_identity(
                    role_name=role,
                    query_user=query_user,
                    password=password,
                    workload_group=workload_group,
                )
                doris_created = True
                return await self._repo.add_query_identity(
                    DorisQueryIdentity(
                        role_name=role,
                        description=description,
                        query_user=query_user,
                        encrypted_password=self._cipher.encrypt(password),
                        workload_group=workload_group,
                        is_default=False,
                    )
                )
        except BaseException as exc:
            if doris_created:
                try:
                    await self._doris_repo.drop_role_identity(
                        role_name=role,
                        query_user=query_user,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(f"补偿删除 Doris 角色及用户失败: {role}")
            if isinstance(exc, DorisQueryUserAlreadyExistsError):
                raise auth_error.QueryUserAlreadyExistsError(
                    detail=f"Doris 查询用户 {exc.query_user} 已存在"
                ) from exc
            if isinstance(exc, IntegrityError):
                raise auth_error.RoleAlreadyExistsError from exc
            if isinstance(exc, DorisRoleAlreadyExistsError):
                raise auth_error.RoleAlreadyExistsError(
                    detail=f"Doris 角色 {role} 已存在"
                ) from exc
            if isinstance(exc, DorisWorkloadGroupNotFoundError):
                raise self._workload_group_not_found(workload_group) from exc
            raise

    async def _require_workload_group(self, workload_group: str) -> None:
        """要求 Doris 工作组存在。"""
        if not await self._doris_repo.workload_group_exists(workload_group):
            raise self._workload_group_not_found(workload_group)

    @staticmethod
    def _workload_group_not_found(
        workload_group: str,
    ) -> auth_error.WorkloadGroupNotFoundError:
        """构造可返回客户端的工作组不存在异常。"""
        return auth_error.WorkloadGroupNotFoundError(
            detail=f"Doris 工作组 {workload_group} 不存在，请选择已创建的工作组"
        )

    async def set_default_role(self, role_name: str) -> DorisQueryIdentity:
        """替换新用户使用的缺省 Doris 角色。"""
        role = normalize_doris_role_name(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            identity = await self._repo.get_query_identity(role)
            if identity is None:
                raise auth_error.RoleNotFoundError
            await self._repo.clear_default_query_identity()
            identity.is_default = True
            await self._repo.flush()
            return identity

    async def clear_default_role(self) -> None:
        """清除新用户使用的缺省 Doris 角色。"""
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._repo.clear_default_query_identity()

    async def delete_role(self, role_name: str) -> None:
        """以跨存储 Saga 删除未被用户使用的 Doris 查询身份和角色。"""
        role = normalize_doris_role_name(role_name)
        snapshot: _RoleDeletionSnapshot | None = None
        drop_state: DorisRoleIdentityDropState | None = None
        try:
            async with self._repo.session.begin():
                await self._repo.lock_security_mutation()
                identity = await self._repo.get_query_identity(role)
                if identity is None:
                    raise auth_error.RoleNotFoundError
                if await self._repo.count_query_identity_assigned_users(role):
                    raise auth_error.RoleInUseError
                grants = await self._repo.list_role_asset_grants(role)
                policies = await self._doris_repo.list_role_row_policies(role)
                snapshot = self._role_deletion_snapshot(identity, grants, policies)
                try:
                    drop_state = await self._doris_repo.drop_role_identity(
                        role_name=identity.role_name,
                        query_user=identity.query_user,
                    )
                except DorisRoleIdentityDropError as exc:
                    drop_state = exc.state
                    raise
                await self._repo.delete_role_asset_grants(role)
                await self._repo.delete_query_identity(identity)
        except BaseException:
            # Doris 不参与 PostgreSQL 事务；包括提交失败和任务取消在内的任一中断都
            # 必须按已完成步骤恢复，避免真实权限与 PostgreSQL 投影长期分离。
            if snapshot is not None and drop_state is not None:
                await self._compensate_role_deletion(snapshot, drop_state)
            raise
        await self._client_registry.invalidate(role)

    def _role_deletion_snapshot(
        self,
        identity: DorisQueryIdentity,
        grants: Sequence[DorisRoleAssetGrant],
        policies: Sequence[DorisRowPolicy],
    ) -> _RoleDeletionSnapshot:
        """从事务对象提取角色删除后的 Doris 恢复状态。"""
        return _RoleDeletionSnapshot(
            role_name=identity.role_name,
            query_user=identity.query_user,
            workload_group=identity.workload_group,
            password=self._cipher.decrypt(identity.encrypted_password),
            select_grants=tuple(
                _RoleSelectGrantSnapshot(
                    scope=grant.scope,
                    database_name=grant.database_name,
                    table_name=grant.table_name,
                    column_name=grant.column_name,
                )
                for grant in grants
            ),
            row_policies=tuple(policies),
        )

    async def _compensate_role_deletion(
        self,
        snapshot: _RoleDeletionSnapshot,
        drop_state: DorisRoleIdentityDropState,
    ) -> None:
        """按 Doris 删除进度恢复查询用户或完整角色状态。"""
        if drop_state.role_deleted:
            await self._restore_role_state(snapshot)
        elif drop_state.query_user_deleted:
            await self._restore_query_user(snapshot)

    async def _restore_query_user(self, snapshot: _RoleDeletionSnapshot) -> None:
        """恢复仍存在角色的查询用户。"""
        try:
            await self._doris_repo.restore_query_user(
                role_name=snapshot.role_name,
                query_user=snapshot.query_user,
                password=snapshot.password,
            )
        except BaseException:  # noqa: BLE001
            logger.exception(
                "Doris 角色删除 Saga 补偿失败: "
                f"role={snapshot.role_name}, query_user={snapshot.query_user}, "
                "stage=restore-query-user"
            )

    async def _restore_role_state(self, snapshot: _RoleDeletionSnapshot) -> None:
        """恢复已删除的 Doris 角色、查询用户、SELECT 权限和行策略。"""
        stage = "restore-role-identity"
        try:
            await self._doris_repo.create_role_identity(
                role_name=snapshot.role_name,
                query_user=snapshot.query_user,
                password=snapshot.password,
                workload_group=snapshot.workload_group,
            )
            stage = "restore-select-grants"
            for database_name, table_name, columns in self._select_grant_targets(
                snapshot.select_grants
            ):
                await self._doris_repo.grant_select(
                    role_name=snapshot.role_name,
                    catalog="internal",
                    database=database_name,
                    table=table_name,
                    columns=columns,
                )
            stage = "restore-row-policies"
            for policy in snapshot.row_policies:
                await self._doris_repo.create_row_policy(
                    policy_name=policy.policy_name,
                    role_name=snapshot.role_name,
                    catalog=policy.catalog_name,
                    database=policy.database_name,
                    table=policy.table_name,
                    policy_type=policy.policy_type,
                    predicate_sql=policy.predicate,
                )
        except BaseException:  # noqa: BLE001
            logger.exception(
                "Doris 角色删除 Saga 补偿失败: "
                f"role={snapshot.role_name}, query_user={snapshot.query_user}, "
                f"stage={stage}"
            )

    @staticmethod
    def _select_grant_targets(
        grants: Sequence[_RoleSelectGrantSnapshot],
    ) -> tuple[tuple[str, str | None, tuple[str, ...]], ...]:
        """将投影快照还原为 Doris 库、表和列级授权操作。"""
        database_grants: set[str] = set()
        table_grants: set[tuple[str, str]] = set()
        column_grants: dict[tuple[str, str], set[str]] = {}
        for grant in grants:
            if grant.scope == AssetScope.DATABASE.value and grant.database_name:
                database_grants.add(grant.database_name)
            elif (
                grant.scope == AssetScope.TABLE.value
                and grant.database_name
                and grant.table_name
            ):
                table_grants.add((grant.database_name, grant.table_name))
            elif (
                grant.scope == AssetScope.COLUMN.value
                and grant.database_name
                and grant.table_name
                and grant.column_name
            ):
                column_grants.setdefault(
                    (grant.database_name, grant.table_name), set()
                ).add(grant.column_name)
            else:
                raise RuntimeError(f"存在无法恢复的 SELECT 权限投影: {grant.scope}")
        targets: list[tuple[str, str | None, tuple[str, ...]]] = [
            (database_name, None, ()) for database_name in sorted(database_grants)
        ]
        targets.extend(
            (database_name, table_name, ())
            for database_name, table_name in sorted(table_grants)
        )
        targets.extend(
            (database_name, table_name, tuple(sorted(columns)))
            for (database_name, table_name), columns in sorted(column_grants.items())
        )
        return tuple(targets)

    async def list_users(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
    ) -> tuple[list[User], int]:
        """分页列出用户与角色并返回总量。"""
        normalized_query = query.strip() if query is not None else None
        if normalized_query == "":
            normalized_query = None
        users = await self._repo.list_users(
            limit=limit,
            offset=offset,
            query=normalized_query,
        )
        total = await self._repo.count_users(query=normalized_query)
        return users, total

    @staticmethod
    def _validate_account_field(
        value: str,
        validator: Callable[[str], str],
    ) -> str:
        """执行账号字段规则并转换为稳定的用户修改错误。"""
        try:
            return validator(value)
        except ValueError as exc:
            raise auth_error.InvalidUserMutationError(detail=str(exc)) from exc

    def _validate_password(self, password: str) -> None:
        """校验管理员写入的密码并转换错误协议。"""
        try:
            validate_password_length(
                password,
                min_length=self._auth_config.password_min_length,
            )
        except ValueError as exc:
            raise auth_error.WeakPasswordError(detail=str(exc)) from exc

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        doris_role: str | None = None,
        is_admin: bool = False,
    ) -> User:
        """平台管理员创建新用户。"""
        normalized_username = self._validate_account_field(username, validate_username)
        normalized_email = self._validate_account_field(email, validate_email)
        self._validate_password(password)

        normalized_role = normalize_doris_role_name(doris_role) if doris_role else None
        password_hash = await self._password_manager.hash(password)
        now = datetime.now(UTC)
        try:
            async with self._repo.session.begin():
                # 串行化角色存在性和用户名/邮箱唯一性检查，防止并发创建基于过期
                # 快照同时提交。
                await self._repo.lock_security_mutation()
                assigned_role: str | None = None
                if normalized_role is not None:
                    identity = await self._repo.get_query_identity(normalized_role)
                    if identity is None:
                        raise auth_error.RoleNotFoundError
                    assigned_role = normalized_role
                else:
                    default_identity = await self._repo.get_default_query_identity()
                    if default_identity is not None:
                        assigned_role = default_identity.role_name
                if (
                    await self._repo.get_user_by_username(normalized_username)
                    is not None
                ):
                    raise auth_error.UsernameAlreadyExistsError
                if await self._repo.get_user_by_email(normalized_email) is not None:
                    raise auth_error.EmailAlreadyExistsError
                user = User(
                    username=normalized_username,
                    email=normalized_email,
                    password_hash=password_hash,
                    is_active=True,
                    is_admin=is_admin,
                    doris_role_name=assigned_role,
                    created_at=now,
                    updated_at=now,
                )
                return await self._repo.add_user(user)
        except IntegrityError as exc:
            raise auth_error.UserAlreadyExistsError from exc

    async def update_user(
        self,
        user_id: int,
        *,
        username: str | None = None,
        email: str | None = None,
        password: str | None = None,
        doris_role: str | None = None,
        update_doris_role: bool = False,
        is_admin: bool | None = None,
    ) -> User:
        """管理员更新指定用户的基础信息、角色、权限或密码并吊销已有令牌。"""
        if doris_role is not None and not update_doris_role:
            raise ValueError("设置 Doris 角色时必须显式启用角色更新")
        normalized_username: str | None = None
        if username is not None:
            normalized_username = self._validate_account_field(
                username,
                validate_username,
            )

        normalized_email: str | None = None
        if email is not None:
            normalized_email = self._validate_account_field(email, validate_email)

        password_hash: str | None = None
        if password is not None:
            self._validate_password(password)
            password_hash = await self._password_manager.hash(password)

        normalized_doris_role: str | None = None
        if update_doris_role and doris_role:
            normalized_doris_role = normalize_doris_role_name(doris_role)

        now = datetime.now(UTC)
        try:
            async with self._repo.session.begin():
                # 角色、最后管理员和唯一性检查与用户更新共享安全锁；刷新令牌也在
                # 同一事务吊销，提交后旧身份立即失效。
                await self._repo.lock_security_mutation()
                if normalized_doris_role is not None:
                    identity_role = await self._repo.get_query_identity(
                        normalized_doris_role
                    )
                    if identity_role is None:
                        raise auth_error.RoleNotFoundError

                user = await self._repo.get_user_by_id(user_id)
                if user is None:
                    raise auth_error.UserNotFoundError

                if (
                    is_admin is not None
                    and user.is_admin
                    and not is_admin
                    and await self._repo.count_admins() <= 1
                ):
                    raise auth_error.LastAdministratorError

                if (
                    normalized_username is not None
                    and normalized_username != user.username
                ):
                    existing = await self._repo.get_user_by_username(
                        normalized_username
                    )
                    if existing is not None and existing.id != user.id:
                        raise auth_error.UsernameAlreadyExistsError

                if normalized_email is not None and normalized_email != user.email:
                    existing_email = await self._repo.get_user_by_email(
                        normalized_email
                    )
                    if existing_email is not None and existing_email.id != user.id:
                        raise auth_error.EmailAlreadyExistsError

                await self._repo.update_user(
                    user,
                    username=normalized_username,
                    email=normalized_email,
                    password_hash=password_hash,
                    doris_role=normalized_doris_role,
                    update_doris_role=update_doris_role,
                    is_admin=is_admin,
                )
                await self._repo.revoke_user_refresh_tokens(user.id, now)
                updated = await self._repo.get_user_by_id(user.id)
                if updated is None:
                    raise RuntimeError("更新后的用户记录无法重新加载")
                return updated
        except IntegrityError as exc:
            raise auth_error.UserAlreadyExistsError from exc

    async def list_asset_grants(
        self,
        role_name: str,
    ) -> list[DorisRoleAssetGrant]:
        """列出 Doris 角色的 SELECT 权限投影。"""
        normalized_name = normalize_doris_role_name(role_name)
        if await self._repo.get_query_identity(normalized_name) is None:
            raise auth_error.RoleNotFoundError
        return await self._repo.list_role_asset_grants(normalized_name)
