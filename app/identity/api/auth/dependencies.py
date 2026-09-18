"""认证与授权接口依赖。"""

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import WebResourcesDep
from app.identity import errors as auth_error
from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.auth import (
    AccessTokenAuthenticator,
    Argon2PasswordManager,
    AuthenticatedUser,
    AuthService,
)
from app.identity.services.authorization import (
    AuthorizationService,
    DorisRoleManagementService,
)
from app.identity.services.credential import DorisCredentialCipher
from app.identity.services.doris_permission import DorisPermissionService
from app.identity.services.rate_limit import AuthRateLimitService
from app.shared.config.app_config import cfg
from app.workflows.user_deletion import UserDeletionService


async def _get_session(resources: WebResourcesDep) -> AsyncGenerator[AsyncSession]:
    """创建当前应用的请求级身份会话。"""
    async with resources.auth.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(_get_session)]


def _get_identity_repo(session: SessionDep) -> IdentityPGRepo:
    """创建请求级 PostgreSQL 身份存储。"""
    return IdentityPGRepo(session)


IdentityRepoDep = Annotated[
    IdentityPGRepo,
    Depends(_get_identity_repo),
]


@lru_cache(maxsize=1)
def _get_password_manager() -> Argon2PasswordManager:
    """创建进程级密码哈希器。"""
    return Argon2PasswordManager()


def _get_auth_service(
    session: SessionDep,
    password_manager: Annotated[
        Argon2PasswordManager,
        Depends(_get_password_manager),
    ],
) -> AuthService:
    """创建请求级认证服务。"""
    return AuthService(
        IdentityPGRepo(session),
        cfg.auth,
        password_manager,
    )


AuthServiceDep = Annotated[AuthService, Depends(_get_auth_service)]


def _get_auth_rate_limit_service(resources: WebResourcesDep) -> AuthRateLimitService:
    """创建跨 API Worker 共享计数的认证限流服务。"""
    return resources.auth_rate_limit


AuthRateLimitServiceDep = Annotated[
    AuthRateLimitService,
    Depends(_get_auth_rate_limit_service),
]
_bearer = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    """读取 ASGI 连接提供的客户端地址。"""
    return request.client.host if request.client is not None else "unknown"


async def _get_current_user(
    resources: WebResourcesDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
) -> AuthenticatedUser:
    """解析 Bearer Token 并加载当前用户。"""
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise auth_error.AuthenticationRequiredError
    async with resources.auth.session() as session:
        return await AccessTokenAuthenticator(
            IdentityPGRepo(session),
            cfg.auth,
        ).authenticate(credentials.credentials)


CurrentUserDep = Annotated[AuthenticatedUser, Depends(_get_current_user)]


async def _require_admin(current_user: CurrentUserDep) -> AuthenticatedUser:
    """要求当前用户是平台管理员。"""
    AuthorizationService.require_admin(current_user)
    return current_user


AdminUserDep = Annotated[AuthenticatedUser, Depends(_require_admin)]


async def _require_analysis_access(
    current_user: CurrentUserDep,
    repo: IdentityRepoDep,
) -> AuthenticatedUser:
    """要求当前用户可创建和执行分析。"""
    identity = (
        await repo.get_query_identity(current_user.doris_role_name)
        if current_user.doris_role_name is not None
        else None
    )
    AuthorizationService.require_analysis_access(current_user, identity)
    return current_user


AnalysisUserDep = Annotated[AuthenticatedUser, Depends(_require_analysis_access)]


async def _get_role_management_service(
    resources: WebResourcesDep,
) -> AsyncGenerator[DorisRoleManagementService]:
    """创建独立会话的 Doris 角色管理服务。"""
    async with resources.auth.session() as session:
        yield DorisRoleManagementService(
            IdentityPGRepo(session),
            DorisRoleRepository(resources.admin_doris),
            _get_doris_credential_cipher(),
            resources.query_clients,
            _get_password_manager(),
            cfg.auth,
        )


DorisRoleManagementServiceDep = Annotated[
    DorisRoleManagementService,
    Depends(_get_role_management_service),
]


def _get_user_deletion_service(resources: WebResourcesDep) -> UserDeletionService:
    """获取进程级跨存储用户注销服务。"""
    return resources.user_deletion


UserDeletionServiceDep = Annotated[
    UserDeletionService,
    Depends(_get_user_deletion_service),
]


async def _get_doris_permission_service(
    resources: WebResourcesDep,
) -> AsyncGenerator[DorisPermissionService]:
    """创建 Doris 权限管理服务。"""
    async with resources.auth.session() as session:
        yield DorisPermissionService(
            IdentityPGRepo(session),
            DorisRoleRepository(resources.admin_doris),
            data_source=cfg.query.data_source,
            catalog="internal",
            database=cfg.doris.database,
        )


DorisPermissionServiceDep = Annotated[
    DorisPermissionService,
    Depends(_get_doris_permission_service),
]


@lru_cache(maxsize=1)
def _get_doris_credential_cipher() -> DorisCredentialCipher:
    """创建进程级 Doris 查询凭据加密器。"""
    return DorisCredentialCipher(
        cfg.doris_credentials.encryption_key.get_secret_value()
    )
