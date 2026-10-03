"""Doris 角色与稳定查询身份管理。"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.identity import errors as auth_error
from app.identity.errors import (
    DorisQueryUserAlreadyExistsError,
    DorisRoleAlreadyExistsError,
    DorisWorkloadGroupNotFoundError,
)
from app.identity.models.doris import (
    DorisQueryIdentity,
    normalize_doris_role_name,
)
from app.identity.repositories.doris_role import (
    DorisRoleRepository,
    role_name_from_row,
    role_users_from_row,
)
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.roles.credentials import DorisCredentialCipher
from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.contracts.doris import validate_doris_identifier


@dataclass(frozen=True, slots=True)
class DorisExistingRoleDescriptor:
    """Doris 中已存在的角色及平台管理状态。"""

    name: str
    managed: bool
    doris_users: tuple[str, ...]


class DorisRoleManagementService:
    """管理 Doris 角色、查询凭据、工作组及默认角色。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        doris_repo: DorisRoleRepository,
        cipher: DorisCredentialCipher,
        client_registry: DorisQueryClientRegistry,
    ) -> None:
        """初始化 Doris 角色、凭据和用户绑定管理依赖。"""
        self._repo = repo
        self._doris_repo = doris_repo
        self._cipher = cipher
        self._client_registry = client_registry

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
        validate_doris_identifier(query_user)
        validate_doris_identifier(workload_group)
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
