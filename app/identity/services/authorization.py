"""RBAC 与数据资产白名单授权服务"""

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.identity import errors as auth_error
from app.identity.models import (
    AssetScope,
    DorisQueryIdentity,
    DorisRoleAssetGrant,
    User,
    asset_resource_key,
    normalize_doris_role_name,
)
from app.identity.repositories.auth import AuthPGRepo
from app.identity.repositories.doris_role import DorisRoleRepository, role_name_from_row
from app.identity.repositories.query_identity import DorisQueryIdentityPGRepo
from app.identity.services.auth import (
    _EMAIL_PATTERN,
    _MAX_EMAIL_LENGTH,
    _MAX_PASSWORD_LENGTH,
    _USERNAME_PATTERN,
    Argon2PasswordManager,
    AuthenticatedUser,
    PasswordManager,
)
from app.identity.services.credential import DorisCredentialCipher
from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.config.app_config import AuthConfig, cfg


@dataclass(frozen=True, slots=True)
class DorisDiscoveredRole:
    """Doris 原生角色扫描结果"""

    name: str
    is_attached: bool
    description: str | None = None
    query_user: str | None = None
    workload_group: str | None = None


@dataclass(frozen=True)
class AssetIdentity:
    """层级化数据资产标识"""

    data_source: str
    database_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None

    def __post_init__(self) -> None:
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
        """返回资产层级"""
        if self.column_name is not None:
            return AssetScope.COLUMN
        if self.table_name is not None:
            return AssetScope.TABLE
        if self.database_name is not None:
            return AssetScope.DATABASE
        return AssetScope.DATA_SOURCE

    @property
    def resource_key(self) -> str:
        """返回无歧义的持久化资源键"""
        return asset_resource_key(
            self.data_source,
            self.database_name,
            self.table_name,
            self.column_name,
        )

    def encompasses(self, other: "AssetIdentity") -> bool:
        """判断当前授权是否覆盖目标资产"""
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

    def as_dict(self) -> dict[str, str | None]:
        """转换为错误响应可用的结构"""
        return {
            "scope": self.scope.value,
            "data_source": self.data_source,
            "database_name": self.database_name,
            "table_name": self.table_name,
            "column_name": self.column_name,
        }


@dataclass(frozen=True)
class AssetAccessPolicy:
    """用户资产访问策略快照"""

    user_id: int
    grants: frozenset[AssetIdentity] = frozenset()
    unrestricted: bool = False

    def allows(self, asset: AssetIdentity) -> bool:
        """判断是否拥有目标资产的完整访问权"""
        return self.unrestricted or any(
            grant.encompasses(asset) for grant in self.grants
        )

    def is_visible(self, asset: AssetIdentity) -> bool:
        """判断资产或其任一下级资产是否可见"""
        return self.allows(asset) or any(
            asset.encompasses(grant) for grant in self.grants
        )

    def require(self, asset: AssetIdentity) -> None:
        """要求目标资产具备完整访问权"""
        if not self.allows(asset):
            raise auth_error.AssetAccessDeniedError(
                detail="该数据资产不在用户授权白名单范围内",
                extensions={"asset": asset.as_dict()},
            )


class AuthorizationService:
    """为检索与 SQL 守卫提供用户授权策略"""

    def __init__(self, repo: AuthPGRepo) -> None:
        self._repo = repo

    async def get_asset_policy(self, user_id: int) -> AssetAccessPolicy:
        """构建用户当前资产访问策略"""
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        if user.doris_role_name is None:
            return AssetAccessPolicy(user_id=user.id)
        grants = await self._repo.list_role_asset_grants(user.doris_role_name)
        return AssetAccessPolicy(
            user_id=user.id,
            grants=frozenset(self._grant_identity(grant) for grant in grants),
        )

    @staticmethod
    def require_admin(user: AuthenticatedUser) -> None:
        """要求用户是平台管理员"""
        if not user.is_admin:
            raise auth_error.PermissionDeniedError(
                detail="需要平台管理员权限"
            )

    @staticmethod
    def require_analysis_access(
        user: AuthenticatedUser,
        identity: DorisQueryIdentity | None,
    ) -> None:
        """要求用户绑定了启用的 Doris 查询身份"""
        if user.doris_role_name is None or identity is None or not identity.is_active:
            raise auth_error.PermissionDeniedError(
                detail="分配的 Doris 角色不可用"
            )

    @staticmethod
    def _grant_identity(grant: DorisRoleAssetGrant) -> AssetIdentity:
        """将持久化授权转换为资产标识"""
        identity = AssetIdentity(
            data_source=grant.data_source,
            database_name=grant.database_name,
            table_name=grant.table_name,
            column_name=grant.column_name,
        )
        if identity.scope.value != grant.scope:
            raise RuntimeError(f"Invalid persisted asset grant: {grant.id}")
        return identity


@dataclass(frozen=True, slots=True)
class DorisRoleDescriptor:
    """可分配的 Doris 数据角色"""

    name: str
    description: str
    is_default: bool
    is_active: bool
    query_user: str
    workload_group: str


class DorisRoleManagementService:
    """平台管理员维护用户与 Doris 角色绑定"""

    def __init__(
        self,
        repo: AuthPGRepo,
        identity_repo: DorisQueryIdentityPGRepo,
        doris_repo: DorisRoleRepository,
        cipher: DorisCredentialCipher,
        client_registry: DorisQueryClientRegistry,
        password_manager: PasswordManager | None = None,
        auth_config: AuthConfig | None = None,
    ) -> None:
        if repo.session is not identity_repo.session:
            raise ValueError("认证存储与查询身份存储必须共享同一数据库会话")
        self._repo = repo
        self._identity_repo = identity_repo
        self._doris_repo = doris_repo
        self._cipher = cipher
        self._client_registry = client_registry
        self._password_manager = password_manager or Argon2PasswordManager()
        self._auth_config = auth_config or cfg.auth

    async def list_roles(self) -> list[DorisRoleDescriptor]:
        """列出全部可分配的 Doris 数据角色"""
        identities = await self._identity_repo.list_all()
        return [
            DorisRoleDescriptor(
                name=identity.role_name,
                description=identity.description,
                is_default=identity.is_default,
                is_active=identity.is_active,
                query_user=identity.query_user,
                workload_group=identity.workload_group,
            )
            for identity in identities
        ]

    async def discover_roles(self) -> list[DorisDiscoveredRole]:
        """扫描 Doris 集群中的全部角色及其在平台的接入状态"""
        rows = await self._doris_repo.list_roles()
        identities = await self._identity_repo.list_all()
        identity_map = {identity.role_name: identity for identity in identities}

        discovered: list[DorisDiscoveredRole] = []
        for row in rows:
            role_name = role_name_from_row(row)
            if not role_name or role_name in {"admin", "root"}:
                continue
            identity = identity_map.get(role_name)
            discovered.append(
                DorisDiscoveredRole(
                    name=role_name,
                    is_attached=identity is not None,
                    description=identity.description if identity else None,
                    query_user=identity.query_user if identity else None,
                    workload_group=identity.workload_group if identity else None,
                )
            )
        return sorted(discovered, key=lambda role: (role.is_attached, role.name))

    async def attach_role(
        self,
        *,
        role_name: str,
        description: str,
        workload_group: str = "normal",
        query_user: str | None = None,
        is_default: bool = False,
    ) -> DorisQueryIdentity:
        """为 Doris 已有角色自动创建并绑定查询用户"""
        role = normalize_doris_role_name(role_name)
        self._doris_repo.quote_identifier(workload_group)
        actual_query_user = query_user.strip() if query_user else f"{role}_query_user"
        self._doris_repo.quote_identifier(actual_query_user)
        password = self._cipher.generate_password()
        doris_user_created = False
        try:
            async with self._repo.session.begin():
                await self._repo.lock_security_mutation()
                if await self._identity_repo.get(role) is not None:
                    raise auth_error.RoleAlreadyExistsError(
                        detail=f"Doris 角色 {role} 已存在"
                    )
                if (
                    await self._identity_repo.get_by_query_user(actual_query_user)
                    is not None
                ):
                    raise auth_error.RoleAlreadyExistsError(
                        detail="Doris 查询用户已被占用"
                    )
                await self._doris_repo.verify_configured_roles((role,))
                current_default = await self._identity_repo.get_default()
                if current_default is None:
                    is_default = True
                await self._doris_repo.create_query_user_for_existing_role(
                    role_name=role,
                    query_user=actual_query_user,
                    password=password,
                    workload_group=workload_group,
                )
                doris_user_created = True
                if is_default:
                    await self._identity_repo.clear_default()
                return await self._identity_repo.add(
                    DorisQueryIdentity(
                        role_name=role,
                        description=description,
                        query_user=actual_query_user,
                        encrypted_password=self._cipher.encrypt(password),
                        workload_group=workload_group,
                        is_default=is_default,
                        is_active=True,
                    )
                )
        except BaseException as exc:
            if doris_user_created:
                try:
                    await self._doris_repo.drop_query_user(actual_query_user)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        f"补偿删除 Doris 查询用户失败: {actual_query_user}"
                    )
            if isinstance(exc, IntegrityError):
                raise auth_error.RoleAlreadyExistsError from exc
            raise

    async def create_role(
        self,
        *,
        role_name: str,
        description: str,
        query_user: str,
        workload_group: str,
        is_default: bool,
    ) -> DorisQueryIdentity:
        """创建 Doris 角色及唯一稳定查询身份"""
        role = normalize_doris_role_name(role_name)
        self._doris_repo.quote_identifier(query_user)
        self._doris_repo.quote_identifier(workload_group)
        password = self._cipher.generate_password()
        doris_created = False
        try:
            async with self._repo.session.begin():
                await self._repo.lock_security_mutation()
                if await self._identity_repo.get(role) is not None:
                    raise auth_error.RoleAlreadyExistsError
                if await self._identity_repo.get_by_query_user(query_user) is not None:
                    raise auth_error.RoleAlreadyExistsError(
                        detail="Doris 查询用户已被占用"
                    )
                current_default = await self._identity_repo.get_default()
                if current_default is None:
                    is_default = True
                await self._doris_repo.create_role_identity(
                    role_name=role,
                    query_user=query_user,
                    password=password,
                    workload_group=workload_group,
                )
                doris_created = True
                if is_default:
                    await self._identity_repo.clear_default()
                return await self._identity_repo.add(
                    DorisQueryIdentity(
                        role_name=role,
                        description=description,
                        query_user=query_user,
                        encrypted_password=self._cipher.encrypt(password),
                        workload_group=workload_group,
                        is_default=is_default,
                        is_active=True,
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
                    logger.exception(
                        f"补偿删除 Doris 角色及用户失败: {role}"
                    )
            if isinstance(exc, IntegrityError):
                raise auth_error.RoleAlreadyExistsError from exc
            raise

    async def set_default_role(self, role_name: str) -> DorisQueryIdentity:
        """替换新用户使用的缺省 Doris 角色"""
        role = normalize_doris_role_name(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            identity = await self._identity_repo.get(role)
            if identity is None:
                raise auth_error.RoleNotFoundError
            if not identity.is_active:
                raise auth_error.DefaultRoleRequiredError(
                    detail="默认 Doris 角色必须处于启用状态"
                )
            await self._identity_repo.clear_default()
            identity.is_default = True
            await self._identity_repo.flush()
            return identity

    async def delete_role(self, role_name: str) -> None:
        """删除未被用户使用的非缺省 Doris 查询身份和角色"""
        role = normalize_doris_role_name(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            identity = await self._identity_repo.get(role)
            if identity is None:
                raise auth_error.RoleNotFoundError
            if identity.is_default:
                raise auth_error.DefaultRoleRequiredError
            if await self._identity_repo.count_assigned_users(role):
                raise auth_error.RoleInUseError
            await self._doris_repo.drop_role_identity(
                role_name=identity.role_name,
                query_user=identity.query_user,
            )
            await self._repo.delete_role_asset_grants(role)
            await self._identity_repo.delete(identity)
        await self._client_registry.invalidate(role)

    async def list_users(self, *, limit: int, offset: int) -> tuple[list[User], int]:
        """分页列出用户与角色并返回总量"""
        users = await self._repo.list_users(limit=limit, offset=offset)
        total = await self._repo.count_users()
        return users, total

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        doris_role: str | None = None,
        is_admin: bool = False,
    ) -> User:
        """平台管理员创建新用户"""
        normalized_username = username.strip().casefold()
        if not _USERNAME_PATTERN.match(normalized_username):
            raise auth_error.InvalidUserMutationError(
                detail="用户名只能包含小写字母、数字、点、下划线和连字符"
            )
        normalized_email = email.strip().casefold()
        if (
            not _EMAIL_PATTERN.match(normalized_email)
            or len(normalized_email) > _MAX_EMAIL_LENGTH
        ):
            raise auth_error.InvalidUserMutationError(
                detail="邮箱地址格式无效"
            )
        if len(password) < self._auth_config.password_min_length:
            raise auth_error.WeakPasswordError
        if len(password) > _MAX_PASSWORD_LENGTH:
            raise auth_error.InvalidUserMutationError(
                detail="密码超出最大长度限制"
            )

        normalized_role = (
            normalize_doris_role_name(doris_role) if doris_role else None
        )
        password_hash = await self._password_manager.hash(password)
        now = datetime.now(UTC)
        try:
            async with self._repo.session.begin():
                await self._repo.lock_security_mutation()
                assigned_role: str | None = None
                if normalized_role is not None:
                    identity = await self._identity_repo.get(normalized_role)
                    if identity is None or not identity.is_active:
                        raise auth_error.RoleNotFoundError
                    assigned_role = normalized_role
                else:
                    default_identity = await self._identity_repo.get_default()
                    if default_identity is not None and default_identity.is_active:
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

    async def set_user_doris_role(
        self,
        user_id: int,
        role_name: str,
    ) -> User:
        """替换用户唯一 Doris 角色并吊销已有刷新令牌"""
        normalized_role = normalize_doris_role_name(role_name)
        now = datetime.now(UTC)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            identity = await self._identity_repo.get(normalized_role)
            if identity is None or not identity.is_active:
                raise auth_error.RoleNotFoundError
            user = await self._repo.get_user_by_id(user_id)
            if user is None:
                raise auth_error.UserNotFoundError
            await self._repo.set_user_doris_role(user, normalized_role)
            await self._repo.revoke_user_refresh_tokens(user.id, now)
            updated = await self._repo.get_user_by_id(user.id)
            if updated is None:
                raise RuntimeError("Updated user could not be reloaded")
            return updated

    async def set_user_admin(self, user_id: int, is_admin: bool) -> User:
        """设置平台管理员标志并保护最后一位管理员"""
        now = datetime.now(UTC)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            user = await self._repo.get_user_by_id(user_id)
            if user is None:
                raise auth_error.UserNotFoundError
            if user.is_admin and not is_admin and await self._repo.count_admins() <= 1:
                raise auth_error.LastAdministratorError
            await self._repo.set_user_admin(user, is_admin)
            await self._repo.revoke_user_refresh_tokens(user.id, now)
            updated = await self._repo.get_user_by_id(user.id)
            if updated is None:
                raise RuntimeError("Updated user could not be reloaded")
            return updated

    async def list_asset_grants(
        self,
        role_name: str,
    ) -> list[DorisRoleAssetGrant]:
        """列出 Doris 角色的 SELECT 权限投影"""
        normalized_name = normalize_doris_role_name(role_name)
        if await self._identity_repo.get(normalized_name) is None:
            raise auth_error.RoleNotFoundError
        return await self._repo.list_role_asset_grants(normalized_name)
