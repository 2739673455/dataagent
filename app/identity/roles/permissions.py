"""PostgreSQL 数据角色权限管理服务。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.identity import errors as auth_error
from app.identity.contracts import AssetIdentity
from app.identity.models.business_pg import (
    BusinessPgRowPolicy,
    BusinessPgSelectGrant,
    normalize_business_pg_role_name,
)
from app.identity.repositories.business_pg_role import (
    BusinessPgRoleRepository,
    role_name_from_row,
)
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.roles.authorization import AuthorizationService


@dataclass(frozen=True, slots=True)
class BusinessPgRoleStatus:
    """配置角色在 PostgreSQL 中的实时状态。"""

    name: str
    description: str
    is_default: bool
    query_user: str
    workload_group: str
    exists_in_business_pg: bool
    business_pg_grants: dict[str, Any] | None


class BusinessPgPermissionService:
    """通过独立管理账号维护 PostgreSQL 角色的细粒度权限。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        business_pg_repo: BusinessPgRoleRepository,
        *,
        data_source: str,
        catalog: str,
        database: str,
    ) -> None:
        """初始化 PostgreSQL 权限操作和平台身份依赖。"""
        self._repo = repo
        self._business_pg_repo = business_pg_repo
        self._data_source = data_source
        self._catalog = catalog
        self._database = database
        self._authorization = AuthorizationService(
            repo,
            business_pg_repo,
            data_source=data_source,
            catalog=catalog,
            database=database,
        )

    async def list_roles(self) -> list[BusinessPgRoleStatus]:
        """合并配置角色与 PostgreSQL 实时授权状态。"""
        live_rows = await self._business_pg_repo.list_roles()
        live_by_name = {
            role_name: row
            for row in live_rows
            if (role_name := role_name_from_row(row)) is not None
        }
        identities = await self._repo.list_query_identities()
        return [
            BusinessPgRoleStatus(
                name=identity.role_name,
                description=identity.description,
                is_default=identity.is_default,
                query_user=identity.query_user,
                workload_group=identity.workload_group,
                exists_in_business_pg=identity.role_name in live_by_name,
                business_pg_grants=live_by_name.get(identity.role_name),
            )
            for identity in identities
        ]

    async def list_asset_grants(self, role_name: str) -> list[BusinessPgSelectGrant]:
        """读取专属查询账号当前有效 SELECT 授权。"""
        async with self._repo.session.begin():
            _, snapshot = await self._authorization.observe_role(
                self._normalize_role(role_name)
            )
            return list(snapshot.grants)

    async def grant_select(
        self,
        role_name: str,
        *,
        table_name: str | None,
        columns: Sequence[str],
    ) -> list[BusinessPgSelectGrant]:
        """校验授权目标并授予角色 SELECT 权限，刷新指纹后返回有效授权。"""
        role = self._normalize_role(role_name)
        columns = self._normalize_columns(columns)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._authorization.observe_role(role)
            await self._validate_target(table_name, columns)
            await self._business_pg_repo.grant_select(
                role_name=role,
                catalog=self._catalog,
                database=self._database,
                table=table_name,
                columns=columns,
            )
            _, snapshot = await self._authorization.observe_role(role)
            return list(snapshot.grants)

    async def revoke_select(
        self,
        role_name: str,
        *,
        table_name: str | None,
        columns: Sequence[str],
    ) -> None:
        """撤销角色指定范围的 SELECT 权限，并确认目标已无有效访问权限。"""
        role = self._normalize_role(role_name)
        columns = self._normalize_columns(columns)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._authorization.observe_role(role)
            # 按授权记录执行撤权，同时支持清理已删除表的遗留授权。
            targets = self._assets(table_name, columns)
            await self._business_pg_repo.revoke_select(
                role_name=role,
                catalog=self._catalog,
                database=self._database,
                table=table_name,
                columns=columns,
            )
            _, snapshot = await self._authorization.observe_role(role)
            assets = [
                AssetIdentity(
                    g.data_source, g.database_name, g.table_name, g.column_name
                )
                for g in snapshot.grants
            ]
            if any(
                remaining.encompasses(target) or target.encompasses(remaining)
                for remaining in assets
                for target in targets
            ):
                raise auth_error.InvalidBusinessPgPermissionError(
                    detail="角色撤权后查询账号仍有有效权限，请检查账号直接授权或更高层级授权"
                )

    async def revoke_all_select(self, role_name: str) -> int:
        """清空角色在业务数据库内的 SELECT 授权，返回撤销前的授权条目数。"""
        role = self._normalize_role(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            _, snapshot = await self._authorization.observe_role(role)
            await self._business_pg_repo.revoke_select(
                role_name=role,
                catalog=self._catalog,
                database=self._database,
                table=None,
                columns=(),
            )
            _, updated = await self._authorization.observe_role(role)
            if updated.grants:
                raise auth_error.InvalidBusinessPgPermissionError(
                    detail="角色撤权后账号仍有直接 SELECT 授权，请在 PostgreSQL 中处理"
                )
            return len(snapshot.grants)

    async def list_row_policies(self, role_name: str) -> list[BusinessPgRowPolicy]:
        """确认角色存在并读取其当前行级过滤策略。"""
        role = await self._require_role(role_name)
        return await self._business_pg_repo.list_role_row_policies(role)

    async def create_row_policy(
        self,
        role_name: str,
        *,
        policy_name: str,
        table_name: str,
        policy_type: Literal["RESTRICTIVE", "PERMISSIVE"],
        predicate: str,
    ) -> None:
        """校验过滤谓词并创建角色行级策略，随后刷新授权指纹。"""
        role = self._normalize_role(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._authorization.observe_role(role)
            await self._business_pg_repo.create_row_policy(
                policy_name=policy_name,
                role_name=role,
                catalog=self._catalog,
                database=self._database,
                table=table_name,
                policy_type=policy_type,
                predicate_sql=predicate,
            )
            await self._authorization.observe_role(role)

    async def drop_row_policy(
        self,
        role_name: str,
        *,
        policy_name: str,
        table_name: str,
    ) -> None:
        """删除角色绑定的指定行级策略，并刷新授权指纹。"""
        role = self._normalize_role(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._authorization.observe_role(role)
            await self._business_pg_repo.drop_row_policy(
                policy_name=policy_name,
                role_name=role,
                catalog=self._catalog,
                database=self._database,
                table=table_name,
            )
            await self._authorization.observe_role(role)

    async def _require_role(self, role_name: str) -> str:
        """要求角色存在于稳定查询身份配置。"""
        normalized = self._normalize_role(role_name)
        await self._require_role_exists(normalized)
        return normalized

    @staticmethod
    def _normalize_role(role_name: str) -> str:
        """规范化 PostgreSQL 角色名。"""
        try:
            return normalize_business_pg_role_name(role_name)
        except ValueError as exc:
            raise auth_error.InvalidBusinessPgPermissionError(
                detail="PostgreSQL 角色名无效"
            ) from exc

    async def _require_role_exists(self, role_name: str) -> None:
        """要求规范化角色已配置。"""
        identity = await self._repo.get_query_identity(role_name)
        if identity is None:
            raise auth_error.RoleNotFoundError

    async def _validate_target(
        self,
        table_name: str | None,
        columns: Sequence[str],
    ) -> None:
        """校验表和字段均存在于配置数据库。"""
        if table_name is None:
            if columns:
                raise auth_error.InvalidBusinessPgPermissionError(
                    detail="指定列权限时必须提供目标表"
                )
            return
        actual_columns = await self._business_pg_repo.list_table_columns(
            self._database,
            table_name,
        )
        if not actual_columns:
            raise auth_error.InvalidBusinessPgPermissionError(detail="目标表不存在")
        unknown = sorted(set(columns) - set(actual_columns))
        if unknown:
            raise auth_error.InvalidBusinessPgPermissionError(
                detail="存在未知的目标列: " + ", ".join(unknown)
            )

    def _assets(
        self,
        table_name: str | None,
        columns: Sequence[str],
    ) -> tuple[AssetIdentity, ...]:
        """将授权目标转换为资产标识。"""
        if table_name is None:
            if columns:
                raise auth_error.InvalidBusinessPgPermissionError(
                    detail="指定列权限时必须提供目标表"
                )
            return (AssetIdentity(self._data_source, self._database),)
        if not columns:
            return (AssetIdentity(self._data_source, self._database, table_name),)
        return tuple(
            AssetIdentity(
                self._data_source,
                self._database,
                table_name,
                column,
            )
            for column in columns
        )

    @staticmethod
    def _normalize_columns(columns: Sequence[str]) -> tuple[str, ...]:
        """校验字段列表无重复。"""
        normalized = tuple(column.strip() for column in columns)
        if any(not column for column in normalized):
            raise auth_error.InvalidBusinessPgPermissionError(detail="列名不能为空")
        if len(set(normalized)) != len(normalized):
            raise auth_error.InvalidBusinessPgPermissionError(detail="列名不能重复")
        return normalized
