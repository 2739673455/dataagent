"""Doris 角色、SELECT 权限与行策略管理访问"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.-]{0,127}$")


class DorisAdminConnectionProvider(Protocol):
    """Doris 权限管理连接提供器"""

    def connection(self) -> AsyncConnection: ...


class DorisRoleRepository:
    """通过独立管理身份操作 Doris 内置 RBAC"""

    def __init__(self, provider: DorisAdminConnectionProvider) -> None:
        self._provider = provider

    @staticmethod
    def quote_identifier(identifier: str) -> str:
        """校验并引用 Doris 标识符"""
        if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
            raise ValueError("invalid Doris identifier")
        return f"`{identifier}`"

    @staticmethod
    def quote_role(role_name: str) -> str:
        """校验并引用 Doris 角色名"""
        if _IDENTIFIER_PATTERN.fullmatch(role_name) is None:
            raise ValueError("invalid Doris role name")
        return f"'{role_name}'"

    @classmethod
    def qualified_table(
        cls,
        catalog: str,
        database: str,
        table: str,
    ) -> str:
        """构造完整表标识"""
        return ".".join(
            cls.quote_identifier(part) for part in (catalog, database, table)
        )

    async def list_roles(self) -> list[dict[str, Any]]:
        """读取 Doris 中的全部显式角色"""
        async with self._provider.connection() as connection:
            result = await connection.execute(text("SHOW ROLES"))
            return [dict(row) for row in result.mappings().all()]

    async def create_role_identity(
        self,
        *,
        role_name: str,
        query_user: str,
        password: str,
        workload_group: str,
    ) -> None:
        """创建 Doris 角色、查询用户及 Workload Group 授权"""
        role = self.quote_role(role_name)
        user = self.quote_role(query_user)
        group = self.quote_role(workload_group)
        if not password or not password.isascii() or "'" in password:
            raise ValueError("invalid generated Doris password")
        role_created = False
        try:
            await self._execute(f"CREATE ROLE {role}")
            role_created = True
            await self._execute(
                f"GRANT USAGE_PRIV ON WORKLOAD GROUP {group} TO ROLE {role}"
            )
            await self._execute(
                f"CREATE USER {user} IDENTIFIED BY '{password}' DEFAULT ROLE {role}"
            )
        except BaseException:
            if role_created:
                try:
                    await self._execute(f"DROP ROLE IF EXISTS {role}")
                except Exception:  # noqa: BLE001
                    logger.exception(
                        f"Failed to compensate Doris role creation: {role_name}"
                    )
            raise

    async def create_query_user_for_existing_role(
        self,
        *,
        role_name: str,
        query_user: str,
        password: str,
        workload_group: str,
    ) -> None:
        """为已存在的 Doris 角色创建代理查询用户并授予 Workload Group 权限"""
        role = self.quote_role(role_name)
        user = self.quote_role(query_user)
        group = self.quote_role(workload_group)
        if not password or not password.isascii() or "'" in password:
            raise ValueError("invalid generated Doris password")
        user_created = False
        try:
            await self._execute(
                f"GRANT USAGE_PRIV ON WORKLOAD GROUP {group} TO ROLE {role}"
            )
            await self._execute(
                f"CREATE USER {user} IDENTIFIED BY '{password}' DEFAULT ROLE {role}"
            )
            user_created = True
        except BaseException:
            if user_created:
                try:
                    await self._execute(f"DROP USER IF EXISTS {user}")
                except Exception:  # noqa: BLE001
                    logger.exception(
                        f"Failed to compensate Doris query user creation: {query_user}"
                    )
            raise

    async def drop_query_user(self, query_user: str) -> None:
        """删除 Doris 查询用户"""
        user = self.quote_role(query_user)
        await self._execute(f"DROP USER IF EXISTS {user}")

    async def drop_role_identity(
        self,
        *,
        role_name: str,
        query_user: str,
    ) -> None:
        """删除 Doris 查询用户和角色"""
        user = self.quote_role(query_user)
        role = self.quote_role(role_name)
        await self._execute(f"DROP USER IF EXISTS {user}")
        await self._execute(f"DROP ROLE IF EXISTS {role}")

    async def verify_configured_roles(self, role_names: Sequence[str]) -> None:
        """确认管理账号可查看且 Doris 已创建全部配置角色"""
        rows = await self.list_roles()
        existing = {
            role_name
            for row in rows
            if (role_name := role_name_from_row(row)) is not None
        }
        missing = sorted(set(role_names) - existing)
        if missing:
            raise RuntimeError(
                "Configured Doris roles do not exist: " + ", ".join(missing)
            )

    async def list_role_row_policies(self, role_name: str) -> list[dict[str, Any]]:
        """读取指定角色的全部行策略"""
        role = self.quote_role(role_name)
        async with self._provider.connection() as connection:
            result = await connection.exec_driver_sql(
                f"SHOW ROW POLICY FOR ROLE {role}"
            )
            return [dict(row) for row in result.mappings().all()]

    async def list_table_columns(
        self,
        database: str,
        table: str,
    ) -> tuple[str, ...]:
        """读取目标表全部字段"""
        async with self._provider.connection() as connection:
            result = await connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :database AND table_name = :table "
                    "ORDER BY ordinal_position"
                ),
                {"database": database, "table": table},
            )
            return tuple(map(str, result.scalars().all()))

    async def grant_select(
        self,
        *,
        role_name: str,
        catalog: str,
        database: str,
        table: str | None,
        columns: Sequence[str],
    ) -> None:
        """向 Doris 角色授予库、表或字段 SELECT 权限"""
        role = self.quote_role(role_name)
        if table is None:
            if columns:
                raise ValueError("column grants require a table")
            target = (
                f"{self.quote_identifier(catalog)}."
                f"{self.quote_identifier(database)}.*"
            )
            privilege = "SELECT_PRIV"
        else:
            target = self.qualified_table(catalog, database, table)
            privilege = self._select_privilege(columns)
        await self._execute(
            f"GRANT {privilege} ON {target} TO ROLE {role}"
        )

    async def revoke_select(
        self,
        *,
        role_name: str,
        catalog: str,
        database: str,
        table: str | None,
        columns: Sequence[str],
    ) -> None:
        """从 Doris 角色回收库、表或字段 SELECT 权限"""
        role = self.quote_role(role_name)
        if table is None:
            if columns:
                raise ValueError("column grants require a table")
            target = (
                f"{self.quote_identifier(catalog)}."
                f"{self.quote_identifier(database)}.*"
            )
            privilege = "SELECT_PRIV"
        else:
            target = self.qualified_table(catalog, database, table)
            privilege = self._select_privilege(columns)
        await self._execute(
            f"REVOKE {privilege} ON {target} FROM ROLE {role}"
        )

    async def create_row_policy(
        self,
        *,
        policy_name: str,
        role_name: str,
        catalog: str,
        database: str,
        table: str,
        policy_type: Literal["RESTRICTIVE", "PERMISSIVE"],
        predicate_sql: str,
    ) -> None:
        """创建绑定 Doris 角色的行策略"""
        policy = self.quote_identifier(policy_name)
        role = self.quote_role(role_name)
        target = self.qualified_table(catalog, database, table)
        await self._execute(
            f"CREATE ROW POLICY {policy} ON {target} AS {policy_type} "
            f"TO ROLE {role} USING ({predicate_sql})"
        )

    async def drop_row_policy(
        self,
        *,
        policy_name: str,
        role_name: str,
        catalog: str,
        database: str,
        table: str,
    ) -> None:
        """删除绑定 Doris 角色的行策略"""
        policy = self.quote_identifier(policy_name)
        role = self.quote_role(role_name)
        target = self.qualified_table(catalog, database, table)
        await self._execute(
            f"DROP ROW POLICY {policy} ON {target} FOR ROLE {role}"
        )

    async def _execute(self, sql: str) -> None:
        """执行完全由已校验结构组成的 Doris 管理语句"""
        async with self._provider.connection() as connection:
            await connection.exec_driver_sql(sql)

    @classmethod
    def _select_privilege(cls, columns: Sequence[str]) -> str:
        """构造表级或列级 SELECT 权限表达式"""
        if not columns:
            return "SELECT_PRIV"
        quoted_columns = ",".join(cls.quote_identifier(column) for column in columns)
        return f"SELECT_PRIV({quoted_columns})"


def role_name_from_row(row: Mapping[str, object]) -> str | None:
    """从不同 Doris 小版本的 SHOW ROLES 结果读取角色名"""
    for key in ("Name", "Role", "RoleName"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return None
