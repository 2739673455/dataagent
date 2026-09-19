"""Doris 数据角色权限管理服务。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import sqlglot
from sqlglot.errors import ParseError

from app.identity import errors as auth_error
from app.identity.models.doris import (
    AssetScope,
    DorisRowPolicy,
    DorisSelectGrant,
    normalize_doris_role_name,
)
from app.identity.repositories.doris_role import DorisRoleRepository, role_name_from_row
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.authorization import AssetIdentity, AuthorizationService


@dataclass(frozen=True, slots=True)
class DorisRoleStatus:
    """配置角色在 Doris 中的实时状态。"""

    name: str
    description: str
    is_default: bool
    query_user: str
    workload_group: str
    exists_in_doris: bool
    doris_grants: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class _SelectGrantTarget:
    """描述一次可直接提交给 Doris 的 SELECT 权限目标。"""

    table_name: str | None
    columns: tuple[str, ...]


class DorisPermissionService:
    """通过独立管理账号维护 Doris 角色的细粒度权限。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        doris_repo: DorisRoleRepository,
        *,
        data_source: str,
        catalog: str,
        database: str,
    ) -> None:
        """初始化 Doris 权限操作和平台身份依赖。"""
        self._repo = repo
        self._doris_repo = doris_repo
        self._data_source = data_source
        self._catalog = catalog
        self._database = database
        self._authorization = AuthorizationService(
            repo,
            doris_repo,
            data_source=data_source,
            catalog=catalog,
            database=database,
        )

    async def list_roles(self) -> list[DorisRoleStatus]:
        """合并配置角色与 Doris 实时授权状态。"""
        live_rows = await self._doris_repo.list_roles()
        live_by_name = {
            role_name: row
            for row in live_rows
            if (role_name := role_name_from_row(row)) is not None
        }
        identities = await self._repo.list_query_identities()
        return [
            DorisRoleStatus(
                name=identity.role_name,
                description=identity.description,
                is_default=identity.is_default,
                query_user=identity.query_user,
                workload_group=identity.workload_group,
                exists_in_doris=identity.role_name in live_by_name,
                doris_grants=live_by_name.get(identity.role_name),
            )
            for identity in identities
        ]

    async def list_asset_grants(self, role_name: str) -> list[DorisSelectGrant]:
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
    ) -> list[DorisSelectGrant]:
        role = self._normalize_role(role_name)
        columns = self._normalize_columns(columns)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._authorization.observe_role(role)
            await self._validate_target(table_name, columns)
            await self._doris_repo.grant_select(
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
        role = self._normalize_role(role_name)
        columns = self._normalize_columns(columns)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._authorization.observe_role(role)
            # 撤权不依赖目标表仍存在，允许清理已删除表的遗留授权。
            self._assets(table_name, columns)
            await self._doris_repo.revoke_select(
                role_name=role,
                catalog=self._catalog,
                database=self._database,
                table=table_name,
                columns=columns,
            )
            _, snapshot = await self._authorization.observe_role(role)
            targets = self._assets(table_name, columns)
            if any(
                AssetIdentity(
                    g.data_source, g.database_name, g.table_name, g.column_name
                ).encompasses(target)
                for g in snapshot.grants
                for target in targets
            ):
                raise auth_error.InvalidDorisPermissionError(
                    detail="角色撤权后查询账号仍有有效权限，请检查账号直接授权或更高层级授权"
                )

    async def revoke_all_select(self, role_name: str) -> int:
        role = self._normalize_role(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            _, snapshot = await self._authorization.observe_role(role)
            if snapshot.has_broad_select:
                raise auth_error.InvalidDorisPermissionError(
                    detail="账号存在全局或 Catalog SELECT 授权，不能在业务数据库范围内清空，请先在 Doris 撤销上级授权"
                )
            for target in self._group_select_grant_targets(snapshot.grants):
                await self._doris_repo.revoke_select(
                    role_name=role,
                    catalog=self._catalog,
                    database=self._database,
                    table=target.table_name,
                    columns=target.columns,
                )
            _, updated = await self._authorization.observe_role(role)
            if updated.grants:
                raise auth_error.InvalidDorisPermissionError(
                    detail="角色撤权后账号仍有直接 SELECT 授权，请在 Doris 中处理"
                )
            return len(snapshot.grants)

    async def list_row_policies(self, role_name: str) -> list[DorisRowPolicy]:
        role = await self._require_role(role_name)
        return await self._doris_repo.list_role_row_policies(role)

    async def create_row_policy(
        self,
        role_name: str,
        *,
        policy_name: str,
        table_name: str,
        policy_type: Literal["RESTRICTIVE", "PERMISSIVE"],
        predicate: str,
    ) -> None:
        role = self._normalize_role(role_name)
        predicate_sql = self._validate_predicate(predicate)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._authorization.observe_role(role)
            await self._doris_repo.create_row_policy(
                policy_name=policy_name,
                role_name=role,
                catalog=self._catalog,
                database=self._database,
                table=table_name,
                policy_type=policy_type,
                predicate_sql=predicate_sql,
            )
            await self._authorization.observe_role(role)

    async def drop_row_policy(
        self,
        role_name: str,
        *,
        policy_name: str,
        table_name: str,
    ) -> None:
        role = self._normalize_role(role_name)
        async with self._repo.session.begin():
            await self._repo.lock_security_mutation()
            await self._authorization.observe_role(role)
            await self._doris_repo.drop_row_policy(
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
        """规范化 Doris 角色名。"""
        try:
            return normalize_doris_role_name(role_name)
        except ValueError as exc:
            raise auth_error.InvalidDorisPermissionError(
                detail="Doris 角色名无效"
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
                raise auth_error.InvalidDorisPermissionError(
                    detail="指定列权限时必须提供目标表"
                )
            return
        actual_columns = await self._doris_repo.list_table_columns(
            self._database,
            table_name,
        )
        if not actual_columns:
            raise auth_error.InvalidDorisPermissionError(detail="目标表不存在")
        unknown = sorted(set(columns) - set(actual_columns))
        if unknown:
            raise auth_error.InvalidDorisPermissionError(
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
                raise auth_error.InvalidDorisPermissionError(
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
    def _group_select_grant_targets(
        grants: Sequence[DorisSelectGrant],
    ) -> tuple[_SelectGrantTarget, ...]:
        """将有效授权合并为数据库、整表和字段级 Doris 回收目标。"""
        has_database_grant = False
        table_grants: set[str] = set()
        column_grants: dict[str, set[str]] = {}

        for grant in grants:
            if grant.scope == AssetScope.DATABASE.value:
                has_database_grant = True
                continue
            if grant.scope == AssetScope.TABLE.value and grant.table_name is not None:
                table_grants.add(grant.table_name)
                continue
            if (
                grant.scope == AssetScope.COLUMN.value
                and grant.table_name is not None
                and grant.column_name is not None
            ):
                column_grants.setdefault(grant.table_name, set()).add(grant.column_name)
                continue
            raise RuntimeError(f"存在无法回收的 SELECT 权限: {grant.scope}")

        targets: list[_SelectGrantTarget] = []
        if has_database_grant:
            targets.append(_SelectGrantTarget(table_name=None, columns=()))
        targets.extend(
            _SelectGrantTarget(table_name=table_name, columns=())
            for table_name in sorted(table_grants)
        )
        targets.extend(
            _SelectGrantTarget(
                table_name=table_name,
                columns=tuple(sorted(columns)),
            )
            for table_name, columns in sorted(column_grants.items())
        )
        return tuple(targets)

    @staticmethod
    def _normalize_columns(columns: Sequence[str]) -> tuple[str, ...]:
        """校验字段列表无重复。"""
        normalized = tuple(column.strip() for column in columns)
        if any(not column for column in normalized):
            raise auth_error.InvalidDorisPermissionError(detail="列名不能为空")
        if len(set(normalized)) != len(normalized):
            raise auth_error.InvalidDorisPermissionError(detail="列名不能重复")
        return normalized

    @staticmethod
    def _validate_predicate(
        predicate: str,
    ) -> str:
        """确认行策略输入是单个 SQL 表达式，并保留原始语义。"""
        normalized = predicate.strip()
        if not normalized:
            raise auth_error.InvalidDorisPermissionError(
                detail="行级策略谓词表达式不能为空"
            )
        try:
            statements = sqlglot.parse(normalized, read="doris")
        except ParseError as exc:
            raise auth_error.InvalidDorisPermissionError(
                detail="行级策略谓词表达式语法无效"
            ) from exc
        if len(statements) != 1:
            raise auth_error.InvalidDorisPermissionError(
                detail="行级策略谓词必须为单个布尔表达式"
            )
        return normalized
