"""认证与授权接口依赖"""

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.doris_client_manager import security_admin_doris_client_manager
from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.conf.app_config import cfg
from app.entities.auth import User
from app.errors import auth_error
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.doris_role_repo import DorisRoleRepository
from app.services.auth_rate_limit_service import AuthRateLimitService
from app.services.auth_service import Argon2PasswordManager, AuthService
from app.services.authorization_service import (
    AuthorizationService,
    DorisRoleManagementService,
)
from app.services.doris_permission_service import DorisPermissionService

SessionDep = Annotated[
    AsyncSession,
    Depends(meta_postgres_client_manager.get_session),
]


def get_auth_repo(session: SessionDep) -> AuthPGRepo:
    """创建请求级认证数据访问"""
    return AuthPGRepo(session)


AuthRepoDep = Annotated[AuthPGRepo, Depends(get_auth_repo)]


@lru_cache(maxsize=1)
def get_password_manager() -> Argon2PasswordManager:
    """创建进程级密码哈希器"""
    return Argon2PasswordManager()


def get_auth_service(
    repo: AuthRepoDep,
    password_manager: Annotated[
        Argon2PasswordManager,
        Depends(get_password_manager),
    ],
) -> AuthService:
    """创建请求级认证服务"""
    return AuthService(
        repo,
        cfg.auth,
        password_manager,
        default_doris_role=cfg.default_doris_role,
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
    service: AuthServiceDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
) -> User:
    """解析 Bearer Token 并加载当前用户"""
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise auth_error.AuthenticationRequiredError
    return await service.authenticate_access_token(credentials.credentials)


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def require_admin(current_user: CurrentUserDep) -> User:
    """要求当前用户是平台管理员"""
    AuthorizationService.require_admin(current_user)
    return current_user


AdminUserDep = Annotated[User, Depends(require_admin)]


async def require_analysis_access(current_user: CurrentUserDep) -> User:
    """要求当前用户可创建和执行分析"""
    AuthorizationService.require_analysis_access(current_user, cfg.doris_roles)
    return current_user


AnalysisUserDep = Annotated[User, Depends(require_analysis_access)]


async def get_authorization_service() -> AsyncGenerator[AuthorizationService]:
    """创建独立会话的资产授权服务"""
    async with meta_postgres_client_manager.session() as session:
        yield AuthorizationService(AuthPGRepo(session))


AuthorizationServiceDep = Annotated[
    AuthorizationService,
    Depends(get_authorization_service),
]


async def get_role_management_service() -> AsyncGenerator[DorisRoleManagementService]:
    """创建独立会话的 Doris 角色管理服务"""
    async with meta_postgres_client_manager.session() as session:
        yield DorisRoleManagementService(AuthPGRepo(session), cfg.doris_roles)


DorisRoleManagementServiceDep = Annotated[
    DorisRoleManagementService,
    Depends(get_role_management_service),
]


async def get_doris_permission_service() -> AsyncGenerator[DorisPermissionService]:
    """创建 Doris 权限管理服务"""
    async with meta_postgres_client_manager.session() as session:
        yield DorisPermissionService(
            AuthPGRepo(session),
            DorisRoleRepository(security_admin_doris_client_manager),
            cfg.doris_roles,
            data_source=cfg.query.data_source,
            catalog="internal",
            database=cfg.doris.database,
        )


DorisPermissionServiceDep = Annotated[
    DorisPermissionService,
    Depends(get_doris_permission_service),
]
