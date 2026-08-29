"""Doris 角色、SELECT 权限与行策略管理访问"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, cast

from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.identity.models.doris import DorisRowPolicy

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.-]{0,127}$")
_USER_IDENTITY_PATTERN = re.compile(r"'(?:\\.|''|[^'])*'@'(?:\\.|''|[^'])*'")


class DorisWorkloadGroupNotFoundError(RuntimeError):
    """Doris 工作组不存在"""

    def __init__(self, workload_group: str) -> None:
        """初始化缺失的 Doris 工作组名称"""
        self.workload_group = workload_group
        super().__init__(f"Doris 工作组不存在: {workload_group}")


class DorisQueryUserAlreadyExistsError(RuntimeError):
    """Doris 查询用户已存在"""

    def __init__(self, query_user: str) -> None:
        """记录发生冲突的 Doris 查询用户名"""
        self.query_user = query_user
        super().__init__(f"Doris 查询用户已存在: {query_user}")


class DorisRoleAlreadyExistsError(RuntimeError):
    """Doris 角色已存在"""

    def __init__(self, role_name: str) -> None:
        """记录发生冲突的 Doris 角色名"""
        self.role_name = role_name
        super().__init__(f"Doris 角色已存在: {role_name}")


class DorisAdminConnectionProvider(Protocol):
    """Doris 权限管理连接提供器"""

    def connection(self) -> AsyncConnection:
        """创建 Doris 管理操作使用的连接上下文"""
        ...


class DorisRoleRepository:
    """通过独立管理身份操作 Doris 内置 RBAC"""

    def __init__(self, provider: DorisAdminConnectionProvider) -> None:
        """绑定 Doris 管理连接提供器"""
        self._provider = provider

    @staticmethod
    def quote_identifier(identifier: str) -> str:
        """校验并引用 Doris 标识符"""
        if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
            raise ValueError("Doris 标识符无效")
        return f"`{identifier}`"

    @classmethod
    def quote_role(cls, role_name: str) -> str:
        """校验并引用 Doris 角色名"""
        return cls.quote_identifier(role_name)

    @classmethod
    def quote_role_literal(cls, role_name: str) -> str:
        """校验 Doris 角色名并构造字符串字面量"""
        cls.quote_role(role_name)
        return f"'{role_name}'"

    @staticmethod
    def quote_user(user_name: str) -> str:
        """校验并引用 Doris 用户名"""
        if _IDENTIFIER_PATTERN.fullmatch(user_name) is None:
            raise ValueError("Doris 用户名格式无效")
        return f"'{user_name}'"

    @classmethod
    def qualified_table(
        cls,
        catalog: str,
        database: str,
        table: str,
    ) -> str:
        """构造引用后的 catalog.database.table"""
        return (
            f"{cls.quote_identifier(catalog)}."
            f"{cls.quote_identifier(database)}."
            f"{cls.quote_identifier(table)}"
        )

    async def list_roles(self) -> list[dict[str, Any]]:
        """读取 Doris 中的全部显式角色"""
        async with self._provider.connection() as connection:
            result = await connection.execute(text("SHOW ROLES"))
            return [dict(row) for row in result.mappings().all()]

    async def list_role_names(self) -> tuple[str, ...]:
        """读取 Doris 中的全部显式角色名"""
        rows = await self.list_roles()
        names = {
            role_name
            for row in rows
            if (role_name := role_name_from_row(row)) is not None
        }
        return tuple(sorted(names, key=str.casefold))

    async def list_workload_groups(self) -> tuple[str, ...]:
        """读取管理账号可见的 Doris 工作组"""
        async with self._provider.connection() as connection:
            result = await connection.execute(
                text(
                    "SELECT name FROM information_schema.workload_groups ORDER BY name"
                )
            )
            return tuple(map(str, result.scalars().all()))

    async def workload_group_exists(self, workload_group: str) -> bool:
        """确认 Doris 工作组是否存在"""
        self.quote_identifier(workload_group)
        async with self._provider.connection() as connection:
            result = await connection.execute(
                text(
                    "SELECT 1 FROM information_schema.workload_groups "
                    "WHERE name = :workload_group LIMIT 1"
                ),
                {"workload_group": workload_group},
            )
            return result.scalar_one_or_none() is not None

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
        role_literal = self.quote_role_literal(role_name)
        self.quote_user(query_user)
        if not password or not password.isascii() or "'" in password:
            raise ValueError("生成的 Doris 密码格式无效")
        role_created = False
        try:
            await self._create_role(role_name=role_name, role=role)
            role_created = True
            await self._grant_workload_group_usage(
                role=role,
                workload_group=workload_group,
            )
            await self._create_query_user(
                query_user=query_user,
                password=password,
                role_literal=role_literal,
            )
        except BaseException:
            if role_created:
                try:
                    await self._execute(f"DROP ROLE IF EXISTS {role}")
                except Exception:  # noqa: BLE001
                    logger.exception(f"补偿删除 Doris 角色失败: {role_name}")
            raise

    async def drop_role_identity(
        self,
        *,
        role_name: str,
        query_user: str,
    ) -> None:
        """删除 Doris 查询用户和角色"""
        user = self.quote_user(query_user)
        role = self.quote_role(role_name)
        await self._execute(f"DROP USER IF EXISTS {user}")
        await self._execute(f"DROP ROLE IF EXISTS {role}")

    async def verify_configured_roles(self, role_names: Sequence[str]) -> None:
        """确认管理账号可查看且 Doris 已创建全部配置角色"""
        existing = set(await self.list_role_names())
        missing = sorted(set(role_names) - existing)
        if missing:
            raise RuntimeError(f"配置的 Doris 角色不存在: {', '.join(missing)}")

    async def list_role_row_policies(self, role_name: str) -> list[DorisRowPolicy]:
        """读取指定角色的全部行策略"""
        role = self.quote_role(role_name)
        async with self._provider.connection() as connection:
            result = await connection.exec_driver_sql(
                f"SHOW ROW POLICY FOR ROLE {role}"
            )
            return [
                _row_policy_from_row(cast(Mapping[str, object], row))
                for row in result.mappings().all()
            ]

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
                raise ValueError("列级授权必须指定对应的数据表")
            target = (
                f"{self.quote_identifier(catalog)}.{self.quote_identifier(database)}.*"
            )
            privilege = "SELECT_PRIV"
        else:
            target = self.qualified_table(catalog, database, table)
            privilege = self._select_privilege(columns)
        await self._execute(f"GRANT {privilege} ON {target} TO ROLE {role}")

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
                raise ValueError("列级授权必须指定对应的数据表")
            target = (
                f"{self.quote_identifier(catalog)}.{self.quote_identifier(database)}.*"
            )
            privilege = "SELECT_PRIV"
        else:
            target = self.qualified_table(catalog, database, table)
            privilege = self._select_privilege(columns)
        await self._execute(f"REVOKE {privilege} ON {target} FROM ROLE {role}")

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
        await self._execute(f"DROP ROW POLICY {policy} ON {target} FOR ROLE {role}")

    async def _execute(self, sql: str) -> None:
        """执行完全由已校验结构组成的 Doris 管理语句"""
        async with self._provider.connection() as connection:
            await connection.exec_driver_sql(sql)

    async def _grant_workload_group_usage(
        self,
        *,
        role: str,
        workload_group: str,
    ) -> None:
        """向角色授予工作组使用权限并识别工作组删除竞争"""
        group = self.quote_identifier(workload_group)
        try:
            await self._execute(
                f"GRANT USAGE_PRIV ON WORKLOAD GROUP {group} TO ROLE {role}"
            )
        except OperationalError as exc:
            message = str(exc.orig).casefold()
            if re.search(r"\bcan\s*not find workload group\b", message):
                raise DorisWorkloadGroupNotFoundError(workload_group) from exc
            raise

    async def _create_role(self, *, role_name: str, role: str) -> None:
        """创建角色并识别 Doris 角色名冲突"""
        try:
            await self._execute(f"CREATE ROLE {role}")
        except OperationalError as exc:
            message = str(exc.orig).casefold()
            if re.search(r"\brole\s+role:\s*.+\balready exists?\b", message):
                raise DorisRoleAlreadyExistsError(role_name) from exc
            raise

    async def _create_query_user(
        self,
        *,
        query_user: str,
        password: str,
        role_literal: str,
    ) -> None:
        """创建查询用户并识别用户名冲突"""
        user = self.quote_user(query_user)
        try:
            await self._execute(
                f"CREATE USER {user} IDENTIFIED BY '{password}' "
                f"DEFAULT ROLE {role_literal}"
            )
        except OperationalError as exc:
            message = str(exc.orig).casefold()
            if re.search(r"\buser\b.+\balready exists?\b", message):
                raise DorisQueryUserAlreadyExistsError(query_user) from exc
            raise

    @classmethod
    def _select_privilege(cls, columns: Sequence[str]) -> str:
        """构造表级或列级 SELECT 权限表达式"""
        if not columns:
            return "SELECT_PRIV"
        quoted_columns = ",".join(cls.quote_identifier(column) for column in columns)
        return f"SELECT_PRIV({quoted_columns})"


def _row_policy_from_row(row: Mapping[str, object]) -> DorisRowPolicy:
    """将 Doris SHOW ROW POLICY 结果转换为稳定模型"""
    raw_policy_type = str(row["FilterType"]).upper()
    if raw_policy_type not in {"RESTRICTIVE", "PERMISSIVE"}:
        raise ValueError(f"Doris 行策略组合类型无效: {raw_policy_type}")
    return DorisRowPolicy(
        policy_name=str(row["PolicyName"]),
        catalog_name=str(row["CatalogName"]),
        database_name=str(row["DbName"]),
        table_name=str(row["TableName"]),
        policy_type=cast(
            Literal["RESTRICTIVE", "PERMISSIVE"],
            raw_policy_type,
        ),
        predicate=str(row["WherePredicate"]),
    )


def role_name_from_row(row: Mapping[str, object]) -> str | None:
    """从不同 Doris 小版本的 SHOW ROLES 结果读取角色名"""
    for key in ("Name", "Role", "RoleName"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


def role_users_from_row(row: Mapping[str, object]) -> tuple[str, ...]:
    """从 SHOW ROLES 结果读取关联的 Doris 用户身份"""
    value = row.get("Users")
    if value is None:
        return ()
    text = str(value).strip()
    if not text or text.casefold() == "null":
        return ()
    identities = tuple(
        match.group(0) for match in _USER_IDENTITY_PATTERN.finditer(text)
    )
    if identities:
        return identities
    return tuple(item.strip() for item in text.split(",") if item.strip())
