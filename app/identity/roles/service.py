"""PostgreSQL 角色与稳定查询身份管理。"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.identity import errors as auth_error
from app.identity.errors import (
    BusinessPgQueryUserAlreadyExistsError,
    BusinessPgRoleAlreadyExistsError,
    BusinessPgWorkloadGroupNotFoundError,
)
from app.identity.models.business_pg import (
    BusinessPgQueryIdentity,
    normalize_business_pg_role_name,
)
from app.identity.repositories.business_pg_role import (
    BusinessPgRoleRepository,
    role_name_from_row,
    role_users_from_row,
)
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.roles.credentials import BusinessPgCredentialCipher
from app.shared.clients.business_pg_client_manager import BusinessPgQueryClientRegistry
from app.shared.contracts.business_pg import validate_business_pg_identifier


@dataclass(frozen=True, slots=True)
class BusinessPgExistingRoleDescriptor:
    """PostgreSQL 中已存在的角色及平台管理状态。"""

    name: str
    managed: bool
    business_pg_users: tuple[str, ...]


class BusinessPgRoleManagementService:
    """管理 PostgreSQL 角色、查询凭据、工作组及默认角色。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        business_pg_repo: BusinessPgRoleRepository,
        cipher: BusinessPgCredentialCipher,
        client_registry: BusinessPgQueryClientRegistry,
    ) -> None:
        """初始化 PostgreSQL 角色、凭据和用户绑定管理依赖。"""
        self._repo = repo
        self._business_pg_repo = business_pg_repo
        self._cipher = cipher
        self._client_registry = client_registry

    async def list_workload_groups(self) -> tuple[str, ...]:
        """列出创建角色时可选择的 PostgreSQL 查询资源配置。"""
        return await self._business_pg_repo.list_workload_groups()

    async def list_existing_roles(self) -> list[BusinessPgExistingRoleDescriptor]:
        """列出 PostgreSQL 原生角色并标记平台管理状态。"""
        rows = await self._business_pg_repo.list_roles()
        managed_names = {
            identity.role_name for identity in await self._repo.list_query_identities()
        }
        roles = [
            BusinessPgExistingRoleDescriptor(
                name=role_name,
                managed=role_name in managed_names,
                business_pg_users=role_users_from_row(row),
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
    ) -> BusinessPgQueryIdentity:
        """创建 PostgreSQL 角色及唯一稳定查询身份。"""
        role = normalize_business_pg_role_name(role_name)
        validate_business_pg_identifier(query_user)
        validate_business_pg_identifier(workload_group)
        await self._require_workload_group(workload_group)
        password = self._cipher.generate_password()
        business_pg_created = False
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
                        detail=f"PostgreSQL 查询用户 {query_user} 已存在"
                    )
                await self._business_pg_repo.create_role_identity(
                    role_name=role,
                    query_user=query_user,
                    password=password,
                    workload_group=workload_group,
                )
                business_pg_created = True
                return await self._repo.add_query_identity(
                    BusinessPgQueryIdentity(
                        role_name=role,
                        description=description,
                        query_user=query_user,
                        encrypted_password=self._cipher.encrypt(password),
                        workload_group=workload_group,
                        is_default=False,
                    )
                )
        except BaseException as exc:
            if business_pg_created:
                try:
                    await self._business_pg_repo.drop_role_identity(
                        role_name=role,
                        query_user=query_user,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(f"补偿删除 PostgreSQL 角色及用户失败: {role}")
            if isinstance(exc, BusinessPgQueryUserAlreadyExistsError):
                raise auth_error.QueryUserAlreadyExistsError(
                    detail=f"PostgreSQL 查询用户 {exc.query_user} 已存在"
                ) from exc
            if isinstance(exc, IntegrityError):
                raise auth_error.RoleAlreadyExistsError from exc
            if isinstance(exc, BusinessPgRoleAlreadyExistsError):
                raise auth_error.RoleAlreadyExistsError(
                    detail=f"PostgreSQL 角色 {role} 已存在"
                ) from exc
            if isinstance(exc, BusinessPgWorkloadGroupNotFoundError):
                raise self._workload_group_not_found(workload_group) from exc
            raise

    async def set_default_role(self, role_name: str) -> BusinessPgQueryIdentity:
        """替换新用户使用的缺省 PostgreSQL 角色。"""
        role = normalize_business_pg_role_name(role_name)
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
        """清除新用户使用的缺省 PostgreSQL 角色。"""
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._repo.clear_default_query_identity()

    async def delete_role(self, role_name: str) -> None:
        """先删除 PostgreSQL 身份再删除平台配置；中途失败保留配置供重试。"""
        role = normalize_business_pg_role_name(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            identity = await self._repo.lock_query_identity(role)
            if identity is None:
                raise auth_error.RoleNotFoundError
            if await self._repo.count_query_identity_assigned_users(role):
                raise auth_error.RoleInUseError
            await self._business_pg_repo.drop_role_identity(
                role_name=identity.role_name,
                query_user=identity.query_user,
            )
            await self._repo.delete_query_identity(identity)
        await self._client_registry.invalidate(role)

    async def _require_workload_group(self, workload_group: str) -> None:
        """要求 PostgreSQL 查询资源配置存在。"""
        if not await self._business_pg_repo.workload_group_exists(workload_group):
            raise self._workload_group_not_found(workload_group)

    @staticmethod
    def _workload_group_not_found(
        workload_group: str,
    ) -> auth_error.WorkloadGroupNotFoundError:
        """构造可返回客户端的工作组不存在异常。"""
        return auth_error.WorkloadGroupNotFoundError(
            detail=f"PostgreSQL 资源档位 {workload_group} 不存在，请选择 standard 或 reporting"
        )
