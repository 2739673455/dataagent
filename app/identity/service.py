"""供其他模块使用的身份解析用例，认证事务由本模块管理。"""

from app.identity import errors as auth_error
from app.identity.accounts.validation import ensure_active_user
from app.identity.auth.tokens import JWTCodec
from app.identity.contracts import (
    AssetAccessPolicy,
    AuthenticatedUser,
    ResolvedQueryPrincipal,
)
from app.identity.errors import PermissionDeniedError, QueryPrincipalNotConfiguredError
from app.identity.repositories.business_pg_role import BusinessPgRoleRepository
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.roles.authorization import AuthorizationService
from app.identity.roles.credentials import BusinessPgCredentialCipher
from app.shared.clients.business_pg_client_manager import BusinessPgClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg


class IdentityService:
    """在短事务中读取当前授权、更新权限指纹并解析查询凭据。"""

    def __init__(
        self, postgres: PostgresClientManager, business_pg: BusinessPgClientManager
    ):
        """绑定身份存储与 PostgreSQL 客户端，并初始化查询凭据加解密器。"""
        self._postgres = postgres
        self._business_pg_repo = BusinessPgRoleRepository(
            business_pg,
            database=cfg.business_pg.database,
            schema=cfg.business_pg.schema_name,
        )
        self._codec = JWTCodec(cfg.auth)
        self._cipher = BusinessPgCredentialCipher(
            cfg.business_pg_credentials.encryption_key.get_secret_value()
        )

    async def authenticate(self, access_token: str) -> AuthenticatedUser:
        """验证令牌和实时账号状态，在独立会话内构造用户快照。"""
        claims = self._codec.decode_access_token(access_token)
        async with self._postgres.session() as session:
            user = await IdentityPGRepo(session).get_user_by_id(claims.user_id)
            if user is None:
                raise auth_error.InvalidTokenError
            ensure_active_user(user)
            if user.auth_version != claims.auth_version:
                raise auth_error.InvalidTokenError
            return AuthenticatedUser(
                id=user.id,
                username=user.username,
                email=user.email,
                auth_version=user.auth_version,
                is_active=user.is_active,
                is_admin=user.is_admin,
                business_pg_role_name=user.business_pg_role_name,
                created_at=user.created_at,
            )

    @staticmethod
    def require_admin(user: AuthenticatedUser) -> None:
        """要求用户是平台管理员。"""
        if not user.is_admin:
            raise PermissionDeniedError(detail="需要平台管理员权限")

    async def require_analysis_access(self, user: AuthenticatedUser) -> None:
        """在认证模块的短会话中确认用户绑定了可用查询身份。"""
        if user.business_pg_role_name is None:
            raise PermissionDeniedError(detail="分配的 PostgreSQL 角色不可用")
        async with self._postgres.session() as session:
            identity = await IdentityPGRepo(session).get_query_identity(
                user.business_pg_role_name
            )
            if identity is None:
                raise PermissionDeniedError(detail="分配的 PostgreSQL 角色不可用")

    async def asset_policy(self, user_id: int) -> AssetAccessPolicy:
        """每次操作观察实时权限，事务结束后才返回策略快照。"""
        async with self._postgres.session() as session, session.begin():
            repo = IdentityPGRepo(session)
            return await self._authorization(repo).get_asset_policy(user_id)

    async def resolve_query_principal(self, user_id: int) -> ResolvedQueryPrincipal:
        """解析本次查询身份，凭据解密和身份行锁留在认证模块内部。"""
        async with self._postgres.session() as session, session.begin():
            repo = IdentityPGRepo(session)
            user = await repo.get_user_by_id(user_id)
            if user is None:
                raise auth_error.UserNotFoundError
            if not user.is_active:
                raise auth_error.InactiveUserError
            if user.business_pg_role_name is None:
                raise QueryPrincipalNotConfiguredError("用户尚未配置 PostgreSQL 角色")
            try:
                identity, snapshot = await self._authorization(repo).observe_role(
                    user.business_pg_role_name
                )
            except auth_error.RoleNotFoundError as exc:
                raise QueryPrincipalNotConfiguredError("角色查询身份不存在") from exc
            return ResolvedQueryPrincipal(
                role_name=identity.role_name,
                authorization_fingerprint=snapshot.fingerprint,
                query_user=identity.query_user,
                password=self._cipher.decrypt(identity.encrypted_password),
                workload_group=identity.workload_group,
            )

    def _authorization(self, repo: IdentityPGRepo) -> AuthorizationService:
        """使用当前身份仓储和配置的 PostgreSQL 数据范围组装授权服务。"""
        return AuthorizationService(
            repo,
            self._business_pg_repo,
            data_source=cfg.query.data_source,
            database=cfg.business_pg.database,
            catalog=cfg.business_pg.schema_name,
        )
