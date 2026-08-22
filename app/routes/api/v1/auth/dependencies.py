"""认证与授权接口依赖"""

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.doris_client_manager import (
    admin_doris_client_manager,
    query_doris_client_registry,
)
from app.clients.postgres_client_manager import auth_postgres_client_manager
from app.conf.app_config import cfg
from app.errors import auth_error
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.doris_query_identity_pg_repo import DorisQueryIdentityPGRepo
from app.repositories.doris_role_repo import DorisRoleRepository
from app.services.auth_rate_limit_service import AuthRateLimitService
from app.services.auth_service import (
    AccessTokenAuthenticator,
    Argon2PasswordManager,
    AuthenticatedUser,
    AuthService,
)
from app.services.authorization_service import (
    AuthorizationService,
    DorisRoleManagementService,
)
from app.services.doris_credential_service import DorisCredentialCipher
from app.services.doris_permission_service import DorisPermissionService
from app.services.user_deletion_service import (
    UserDeletionService,
    user_deletion_service,
)

SessionDep = Annotated[
    AsyncSession,
    Depends(auth_postgres_client_manager.get_session),
]


def get_query_identity_repo(session: SessionDep) -> DorisQueryIdentityPGRepo:
    """创建请求级 Doris 查询身份访问"""
    return DorisQueryIdentityPGRepo(session)


QueryIdentityRepoDep = Annotated[
    DorisQueryIdentityPGRepo,
    Depends(get_query_identity_repo),
]


@lru_cache(maxsize=1)
def get_password_manager() -> Argon2PasswordManager:
    """创建进程级密码哈希器"""
    return Argon2PasswordManager()


def get_auth_service(
    session: SessionDep,
    password_manager: Annotated[
        Argon2PasswordManager,
        Depends(get_password_manager),
    ],
) -> AuthService:
    """创建请求级认证服务"""
    return AuthService(
        AuthPGRepo(session),
        cfg.auth,
        password_manager,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


@lru_cache(maxsize=1)
def get_auth_rate_limit_service() -> AuthRateLimitService:
    """创建进程级认证限流服务"""
    return AuthRateLimitService()


AuthRateLimitServiceDep = Annotated[
    AuthRateLimitService,
    Depends(get_auth_rate_limit_service),
]
_bearer = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    """读取 ASGI 连接提供的客户端地址"""
    return request.client.host if request.client is not None else "unknown"


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
) -> AuthenticatedUser:
    """解析 Bearer Token 并加载当前用户"""
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise auth_error.AuthenticationRequiredError
    async with auth_postgres_client_manager.session() as session:
        return await AccessTokenAuthenticator(
            AuthPGRepo(session),
            cfg.auth,
        ).authenticate(credentials.credentials)


CurrentUserDep = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def require_admin(current_user: CurrentUserDep) -> AuthenticatedUser:
    """要求当前用户是平台管理员"""
    AuthorizationService.require_admin(current_user)
    return current_user


AdminUserDep = Annotated[AuthenticatedUser, Depends(require_admin)]


async def require_analysis_access(
    current_user: CurrentUserDep,
    identity_repo: QueryIdentityRepoDep,
) -> AuthenticatedUser:
    """要求当前用户可创建和执行分析"""
    identity = (
        await identity_repo.get(current_user.doris_role_name)
        if current_user.doris_role_name is not None
        else None
    )
    AuthorizationService.require_analysis_access(current_user, identity)
    return current_user


AnalysisUserDep = Annotated[AuthenticatedUser, Depends(require_analysis_access)]


async def get_role_management_service() -> AsyncGenerator[DorisRoleManagementService]:
    """创建独立会话的 Doris 角色管理服务"""
    async with auth_postgres_client_manager.session() as session:
        yield DorisRoleManagementService(
            AuthPGRepo(session),
            DorisQueryIdentityPGRepo(session),
            DorisRoleRepository(admin_doris_client_manager),
            get_doris_credential_cipher(),
            query_doris_client_registry,
            get_password_manager(),
            cfg.auth,
        )


DorisRoleManagementServiceDep = Annotated[
    DorisRoleManagementService,
    Depends(get_role_management_service),
]


def get_user_deletion_service() -> UserDeletionService:
    """获取进程级跨存储用户注销服务"""
    return user_deletion_service


UserDeletionServiceDep = Annotated[
    UserDeletionService,
    Depends(get_user_deletion_service),
]


async def get_doris_permission_service() -> AsyncGenerator[DorisPermissionService]:
    """创建 Doris 权限管理服务"""
    async with auth_postgres_client_manager.session() as session:
        yield DorisPermissionService(
            AuthPGRepo(session),
            DorisQueryIdentityPGRepo(session),
            DorisRoleRepository(admin_doris_client_manager),
            data_source=cfg.query.data_source,
            catalog="internal",
            database=cfg.doris.database,
        )


DorisPermissionServiceDep = Annotated[
    DorisPermissionService,
    Depends(get_doris_permission_service),
]


@lru_cache(maxsize=1)
def get_doris_credential_cipher() -> DorisCredentialCipher:
    """创建进程级 Doris 查询凭据加密器"""
    return DorisCredentialCipher(
        cfg.doris_credentials.encryption_key.get_secret_value()
    )
