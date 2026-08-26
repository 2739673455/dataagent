"""Doris 数据角色权限管理服务"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import sqlglot
from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlglot import exp
from sqlglot.errors import ParseError

from app.identity import errors as auth_error
from app.identity.models.doris import (
    AssetScope,
    DorisRoleAssetGrant,
    DorisRowPolicy,
    normalize_doris_role_name,
)
from app.identity.repositories.auth import AuthPGRepo
from app.identity.repositories.doris_role import DorisRoleRepository, role_name_from_row
from app.identity.repositories.query_identity import DorisQueryIdentityPGRepo
from app.identity.services.authorization import AssetIdentity


@dataclass(frozen=True, slots=True)
class DorisRoleStatus:
    """配置角色在 Doris 中的实时状态"""

    name: str
    description: str
    is_default: bool
    query_user: str
    workload_group: str
    exists_in_doris: bool
    doris_grants: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class _SelectGrantTarget:
    """描述一次可直接提交给 Doris 的 SELECT 权限目标"""

    table_name: str | None
    columns: tuple[str, ...]


class DorisPermissionService:
    """通过独立管理账号维护 Doris 角色的细粒度权限"""

    def __init__(
        self,
        auth_repo: AuthPGRepo,
        identity_repo: DorisQueryIdentityPGRepo,
        doris_repo: DorisRoleRepository,
        *,
        data_source: str,
        catalog: str,
        database: str,
    ) -> None:
        """初始化 Doris 权限操作和 PostgreSQL 投影依赖"""
        if auth_repo.session is not identity_repo.session:
            raise ValueError("认证存储与查询身份存储必须共享同一数据库会话")
        self._auth_repo = auth_repo
        self._identity_repo = identity_repo
        self._doris_repo = doris_repo
        self._data_source = data_source
        self._catalog = catalog
        self._database = database

    async def list_roles(self) -> list[DorisRoleStatus]:
        """合并配置角色与 Doris 实时授权状态"""
        live_rows = await self._doris_repo.list_roles()
        live_by_name = {
            role_name: row
            for row in live_rows
            if (role_name := role_name_from_row(row)) is not None
        }
        identities = await self._identity_repo.list_all()
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

    async def grant_select(
        self,
        role_name: str,
        *,
        table_name: str | None,
        columns: Sequence[str],
    ) -> list[DorisRoleAssetGrant]:
        """授予角色库、表或列 SELECT 权限并更新可见性投影"""
        role = self._normalize_role(role_name)
        normalized_columns = self._normalize_columns(columns)
        assets = self._assets(table_name, normalized_columns)
        granted_columns: tuple[str, ...] = ()
        doris_changed = False
        try:
            async with self._auth_repo.session.begin():
                await self._auth_repo.lock_security_mutation()
                await self._require_role_exists(role)
                await self._validate_target(table_name, normalized_columns)
                existing = [
                    await self._auth_repo.find_asset_grant(
                        role,
                        asset.scope.value,
                        asset.resource_key,
                    )
                    for asset in assets
                ]
                if all(grant is not None for grant in existing):
                    return [grant for grant in existing if grant is not None]
                pending_assets = [
                    asset
                    for asset, persisted in zip(assets, existing, strict=True)
                    if persisted is None
                ]
                granted_columns = tuple(
                    asset.column_name
                    for asset in pending_assets
                    if asset.column_name is not None
                )
                await self._doris_repo.grant_select(
                    role_name=role,
                    catalog=self._catalog,
                    database=self._database,
                    table=table_name,
                    columns=granted_columns,
                )
                doris_changed = True
                result: list[DorisRoleAssetGrant] = []
                for asset, persisted in zip(assets, existing, strict=True):
                    if persisted is None:
                        persisted = await self._auth_repo.add_asset_grant(
                            DorisRoleAssetGrant(
                                role_name=role,
                                scope=asset.scope.value,
                                data_source=asset.data_source,
                                database_name=asset.database_name,
                                table_name=asset.table_name,
                                column_name=asset.column_name,
                                resource_key=asset.resource_key,
                            )
                        )
                    result.append(persisted)
                return result
        except IntegrityError as exc:
            if doris_changed:
                await self._compensate_select(
                    grant=False,
                    role_name=role,
                    table_name=table_name,
                    columns=granted_columns,
                )
            raise auth_error.AssetGrantAlreadyExistsError from exc
        except BaseException:
            if doris_changed:
                await self._compensate_select(
                    grant=False,
                    role_name=role,
                    table_name=table_name,
                    columns=granted_columns,
                )
            raise

    async def revoke_select(
        self,
        role_name: str,
        *,
        table_name: str | None,
        columns: Sequence[str],
    ) -> None:
        """回收角色库、表或列 SELECT 权限并删除可见性投影"""
        role = self._normalize_role(role_name)
        normalized_columns = self._normalize_columns(columns)
        assets = self._assets(table_name, normalized_columns)
        doris_changed = False
        try:
            async with self._auth_repo.session.begin():
                await self._auth_repo.lock_security_mutation()
                await self._require_role_exists(role)
                await self._validate_target(table_name, normalized_columns)
                grants = [
                    await self._auth_repo.find_asset_grant(
                        role,
                        asset.scope.value,
                        asset.resource_key,
                    )
                    for asset in assets
                ]
                if any(grant is None for grant in grants):
                    raise auth_error.AssetGrantNotFoundError
                await self._doris_repo.revoke_select(
                    role_name=role,
                    catalog=self._catalog,
                    database=self._database,
                    table=table_name,
                    columns=normalized_columns,
                )
                doris_changed = True
                for grant in grants:
                    if grant is not None:
                        await self._auth_repo.delete_asset_grant(grant)
        except BaseException:
            if doris_changed:
                await self._compensate_select(
                    grant=True,
                    role_name=role,
                    table_name=table_name,
                    columns=normalized_columns,
                )
            raise

    async def revoke_all_select(self, role_name: str) -> int:
        """回收角色在当前数据库中的全部 SELECT 权限并清空投影"""
        role = self._normalize_role(role_name)
        revoked_targets: list[_SelectGrantTarget] = []
        try:
            async with self._auth_repo.session.begin():
                await self._auth_repo.lock_security_mutation()
                await self._require_role_exists(role)
                grants = await self._auth_repo.list_role_asset_grants(role)
                if not grants:
                    return 0

                targets = self._group_select_grant_targets(grants)
                for target in targets:
                    await self._doris_repo.revoke_select(
                        role_name=role,
                        catalog=self._catalog,
                        database=self._database,
                        table=target.table_name,
                        columns=target.columns,
                    )
                    revoked_targets.append(target)

                await self._auth_repo.delete_role_asset_grants(role)
                return len(grants)
        except BaseException:
            for target in reversed(revoked_targets):
                await self._compensate_select(
                    grant=True,
                    role_name=role,
                    table_name=target.table_name,
                    columns=target.columns,
                )
            raise

    async def list_row_policies(self, role_name: str) -> list[DorisRowPolicy]:
        """读取角色在 Doris 中的实时行策略"""
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
        """校验并创建绑定到角色的 Doris 行策略"""
        role = await self._require_role(role_name)
        columns = await self._doris_repo.list_table_columns(
            self._database,
            table_name,
        )
        if not columns:
            raise auth_error.InvalidDorisPermissionError(detail="目标表不存在")
        predicate_sql = self._validate_predicate(predicate, table_name, columns)
        await self._doris_repo.create_row_policy(
            policy_name=policy_name,
            role_name=role,
            catalog=self._catalog,
            database=self._database,
            table=table_name,
            policy_type=policy_type,
            predicate_sql=predicate_sql,
        )

    async def drop_row_policy(
        self,
        role_name: str,
        *,
        policy_name: str,
        table_name: str,
    ) -> None:
        """删除绑定到角色的 Doris 行策略"""
        role = await self._require_role(role_name)
        await self._doris_repo.drop_row_policy(
            policy_name=policy_name,
            role_name=role,
            catalog=self._catalog,
            database=self._database,
            table=table_name,
        )

    async def _require_role(self, role_name: str) -> str:
        """要求角色存在于稳定查询身份配置"""
        normalized = self._normalize_role(role_name)
        await self._require_role_exists(normalized)
        return normalized

    @staticmethod
    def _normalize_role(role_name: str) -> str:
        """规范化 Doris 角色名"""
        try:
            return normalize_doris_role_name(role_name)
        except ValueError as exc:
            raise auth_error.InvalidDorisPermissionError(
                detail="Doris 角色名无效"
            ) from exc

    async def _require_role_exists(self, role_name: str) -> None:
        """要求规范化角色已配置"""
        identity = await self._identity_repo.get(role_name)
        if identity is None:
            raise auth_error.RoleNotFoundError

    async def _compensate_select(
        self,
        *,
        grant: bool,
        role_name: str,
        table_name: str | None,
        columns: Sequence[str],
    ) -> None:
        """在 PostgreSQL 投影失败时尽力恢复 Doris 权限"""
        operation = (
            self._doris_repo.grant_select if grant else self._doris_repo.revoke_select
        )
        try:
            await operation(
                role_name=role_name,
                catalog=self._catalog,
                database=self._database,
                table=table_name,
                columns=columns,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                f"Doris 权限补偿操作失败: role={role_name}, table={table_name}"
            )

    async def _validate_target(
        self,
        table_name: str | None,
        columns: Sequence[str],
    ) -> None:
        """校验表和字段均存在于配置数据库"""
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
        """将授权目标转换为可见性投影资产"""
        if table_name is None:
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
        grants: Sequence[DorisRoleAssetGrant],
    ) -> tuple[_SelectGrantTarget, ...]:
        """将权限投影合并为数据库、整表和字段级 Doris 回收目标"""
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
            raise RuntimeError(f"存在无法回收的 SELECT 权限投影: {grant.scope}")

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
        """校验字段列表无重复"""
        normalized = tuple(column.strip() for column in columns)
        if any(not column for column in normalized):
            raise auth_error.InvalidDorisPermissionError(detail="列名不能为空")
        if len(set(normalized)) != len(normalized):
            raise auth_error.InvalidDorisPermissionError(detail="列名不能重复")
        return normalized

    @staticmethod
    def _validate_predicate(
        predicate: str,
        table_name: str,
        allowed_columns: Sequence[str],
    ) -> str:
        """将行策略限制为目标表上的单个布尔表达式"""
        normalized = predicate.strip()
        if not normalized:
            raise auth_error.InvalidDorisPermissionError(
                detail="行级策略谓词表达式不能为空"
            )
        try:
            statements = sqlglot.parse(
                f"SELECT 1 FROM `{table_name}` WHERE {normalized}",
                read="doris",
            )
        except ParseError as exc:
            raise auth_error.InvalidDorisPermissionError(
                detail="行级策略谓词表达式语法无效"
            ) from exc
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise auth_error.InvalidDorisPermissionError(
                detail="行级策略谓词必须为单个布尔表达式"
            )
        where = statements[0].args.get("where")
        if not isinstance(where, exp.Where):
            raise auth_error.InvalidDorisPermissionError(
                detail="缺少行级策略谓词表达式"
            )
        expression = where.this
        forbidden = (
            exp.Subquery,
            exp.Select,
            exp.Union,
            exp.Placeholder,
            exp.Parameter,
        )
        if any(isinstance(node, forbidden) for node in expression.walk()):
            raise auth_error.InvalidDorisPermissionError(
                detail="行级策略谓词包含禁止的语法结构"
            )
        allowed = set(allowed_columns)
        for column in expression.find_all(exp.Column):
            if column.name not in allowed:
                raise auth_error.InvalidDorisPermissionError(
                    detail=f"行级策略引用了未知的列: {column.name}"
                )
            if column.table and column.table != table_name:
                raise auth_error.InvalidDorisPermissionError(
                    detail="行级策略不能跨表引用其他表"
                )
        return expression.sql(dialect="doris")
