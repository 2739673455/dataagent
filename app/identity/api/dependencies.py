"""账号和 Doris 角色管理接口的请求依赖。"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import WebResourcesDep
from app.identity.accounts.service import UserManagementService
from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.roles.credentials import DorisCredentialCipher
from app.identity.roles.permissions import DorisPermissionService
from app.identity.roles.service import DorisRoleManagementService
from app.shared.config.app_config import cfg
from app.workflows import UserDeletionService


async def _get_session(resources: WebResourcesDep) -> AsyncGenerator[AsyncSession]:
    """为一次身份管理请求创建独立数据库会话。"""
    async with resources.auth.session() as session:
        yield session


IdentitySessionDep = Annotated[AsyncSession, Depends(_get_session)]


def _get_user_management_service(session: IdentitySessionDep) -> UserManagementService:
    """使用当前请求会话创建账号管理服务。"""
    return UserManagementService(IdentityPGRepo(session), cfg.auth)


UserManagementServiceDep = Annotated[
    UserManagementService, Depends(_get_user_management_service)
]


def _get_role_management_service(
    session: IdentitySessionDep, resources: WebResourcesDep
) -> DorisRoleManagementService:
    """使用当前请求会话创建 Doris 角色管理服务。"""
    return DorisRoleManagementService(
        IdentityPGRepo(session),
        DorisRoleRepository(resources.admin_doris),
        DorisCredentialCipher(cfg.doris_credentials.encryption_key.get_secret_value()),
        resources.query_clients,
    )


DorisRoleManagementServiceDep = Annotated[
    DorisRoleManagementService, Depends(_get_role_management_service)
]


def _get_doris_permission_service(
    session: IdentitySessionDep, resources: WebResourcesDep
) -> DorisPermissionService:
    """使用当前请求会话创建 Doris 权限管理服务。"""
    return DorisPermissionService(
        IdentityPGRepo(session),
        DorisRoleRepository(resources.admin_doris),
        data_source=cfg.query.data_source,
        catalog="internal",
        database=cfg.doris.database,
    )


DorisPermissionServiceDep = Annotated[
    DorisPermissionService, Depends(_get_doris_permission_service)
]


def _get_user_deletion_service(resources: WebResourcesDep) -> UserDeletionService:
    """读取应用持有的跨模块用户注销服务。"""
    return resources.user_deletion


UserDeletionServiceDep = Annotated[
    UserDeletionService, Depends(_get_user_deletion_service)
]
