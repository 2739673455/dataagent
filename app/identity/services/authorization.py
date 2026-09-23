"""RBAC 与数据资产白名单授权服务。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.identity import errors as auth_error
from app.identity.errors import (
    DorisQueryUserAlreadyExistsError,
    DorisRoleAlreadyExistsError,
    DorisWorkloadGroupNotFoundError,
)
from app.identity.models.account import User
from app.identity.models.doris import (
    AssetScope,
    DorisAuthorizationSnapshot,
    DorisQueryIdentity,
    normalize_doris_role_name,
)
from app.identity.repositories.doris_role import (
    DorisRoleRepository,
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
from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.config.app_config import AuthConfig
from app.shared.contracts.assets import asset_resource_key


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
    """为检索与 SQL 守卫提供用户授权策略。"""

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


@dataclass(frozen=True, slots=True)
class DorisExistingRoleDescriptor:
    """Doris 中已存在的角色及平台管理状态。"""

    name: str
    managed: bool
    doris_users: tuple[str, ...]


class DorisRoleManagementService:
    """平台管理员维护用户与 Doris 角色绑定。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        doris_repo: DorisRoleRepository,
        cipher: DorisCredentialCipher,
        client_registry: DorisQueryClientRegistry,
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
        """先删除 Doris 身份再删除平台配置；中途失败保留配置供重试。"""
        role = normalize_doris_role_name(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            identity = await self._repo.lock_query_identity(role)
            if identity is None:
                raise auth_error.RoleNotFoundError
            if await self._repo.count_query_identity_assigned_users(role):
                raise auth_error.RoleInUseError
            await self._doris_repo.drop_role_identity(
                role_name=identity.role_name,
                query_user=identity.query_user,
            )
            await self._repo.delete_query_identity(identity)
        await self._client_registry.invalidate(role)

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
