"""Doris 角色、SELECT 权限与行策略管理访问。"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.identity.errors import (
    DorisQueryUserAlreadyExistsError,
    DorisRoleAlreadyExistsError,
    DorisWorkloadGroupNotFoundError,
)
from app.identity.models.doris import DorisAuthorizationSnapshot, DorisRowPolicy
from app.identity.repositories.doris_authorization import parse_authorization
from app.shared.clients.doris_client_manager import DorisClientManager

_USER_IDENTITY_PATTERN = re.compile(r"'(?:\\.|''|[^'])*'@'(?:\\.|''|[^'])*'")


class DorisRoleRepository:
    """通过独立管理身份操作 Doris 内置 RBAC。"""

    def __init__(self, provider: DorisClientManager) -> None:
        """绑定 Doris 管理连接提供器。"""
        self._provider = provider

    async def list_roles(self) -> list[dict[str, Any]]:
        """读取 Doris 中的全部显式角色。"""
        async with self._provider.engine.connect() as connection:
            result = await connection.execute(text("SHOW ROLES"))
            return [dict(row) for row in result.mappings().all()]

    async def read_authorization(
        self,
        *,
        role_name: str,
        query_user: str,
        data_source: str,
        catalog: str,
        database: str,
    ) -> DorisAuthorizationSnapshot:
        """从专属查询账号读取有效权限及角色、用户行策略。"""
        role = self._quote_identifier(role_name)
        async with self._provider.engine.connect() as connection:
            await connection.exec_driver_sql("SET show_user_default_role = false")
            result = await connection.exec_driver_sql(
                "SHOW GRANTS FOR %s@%s", (query_user, "%")
            )
            rows = result.mappings().all()
            if len(rows) != 1:
                raise ValueError("Doris 查询账号授权结果必须唯一")
            policies: list[dict[str, Any]] = []
            for subject, parameters in (
                (f"ROLE {role}", ()),
                ("%s@%s", (query_user, "%")),
            ):
                result = await connection.exec_driver_sql(
                    f"SHOW ROW POLICY FOR {subject}", parameters
                )
                policies.extend(dict(row) for row in result.mappings().all())
            return parse_authorization(
                dict(rows[0]),
                policies,
                role_name=role_name,
                query_user=query_user,
                data_source=data_source,
                catalog=catalog,
                database=database,
            )

    async def list_workload_groups(self) -> tuple[str, ...]:
        """读取管理账号可见的 Doris 工作组。"""
        async with self._provider.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT name FROM information_schema.workload_groups ORDER BY name"
                )
            )
            return tuple(map(str, result.scalars().all()))

    async def workload_group_exists(self, workload_group: str) -> bool:
        """确认 Doris 工作组是否存在。"""
        async with self._provider.engine.connect() as connection:
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
        """创建 Doris 角色、查询用户及 Workload Group 授权。"""
        role = self._quote_identifier(role_name)
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
                role_name=role_name,
            )
        except BaseException:
            if role_created:
                try:
                    await self._execute(f"DROP ROLE IF EXISTS {role}")
                except Exception:  # noqa: BLE001
                    logger.exception(f"补偿删除 Doris 角色失败: {role_name}")
            raise

    async def drop_role_identity(self, *, role_name: str, query_user: str) -> None:
        """幂等删除查询用户和角色；失败后允许再次执行剩余步骤。"""
        role = self._quote_identifier(role_name)
        await self._execute("DROP USER IF EXISTS %s@%s", (query_user, "%"))
        await self._execute(f"DROP ROLE IF EXISTS {role}")

    async def list_role_row_policies(self, role_name: str) -> list[DorisRowPolicy]:
        """读取指定角色的全部行策略。"""
        role = self._quote_identifier(role_name)
        async with self._provider.engine.connect() as connection:
            result = await connection.exec_driver_sql(
                f"SHOW ROW POLICY FOR ROLE {role}", ()
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
        """读取目标表全部字段。"""
        async with self._provider.engine.connect() as connection:
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
        """向 Doris 角色授予库、表或字段 SELECT 权限。"""
        role = self._quote_identifier(role_name)
        if table is None:
            if columns:
                raise ValueError("列级授权必须指定对应的数据表")
            target = f"{self._quote_identifier(catalog)}.{self._quote_identifier(database)}.*"
            privilege = "SELECT_PRIV"
        else:
            target = self._qualified_table(catalog, database, table)
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
        """从 Doris 角色回收库、表或字段 SELECT 权限。"""
        role = self._quote_identifier(role_name)
        if table is None:
            if columns:
                raise ValueError("列级授权必须指定对应的数据表")
            target = f"{self._quote_identifier(catalog)}.{self._quote_identifier(database)}.*"
            privilege = "SELECT_PRIV"
        else:
            target = self._qualified_table(catalog, database, table)
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
        """创建绑定 Doris 角色的行策略。"""
        policy = self._quote_identifier(policy_name)
        role = self._quote_identifier(role_name)
        target = self._qualified_table(catalog, database, table)
        # predicate_sql 是已校验的 SQL 片段；百分号须避开 DBAPI 的格式占位语法。
        predicate_sql = predicate_sql.replace("%", "%%")
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
        """删除绑定 Doris 角色的行策略。"""
        policy = self._quote_identifier(policy_name)
        role = self._quote_identifier(role_name)
        target = self._qualified_table(catalog, database, table)
        await self._execute(f"DROP ROW POLICY {policy} ON {target} FOR ROLE {role}")

    def _quote_identifier(self, identifier: str) -> str:
        """用当前连接方言引用标识符，包含 DBAPI 百分号转义。"""
        return self._provider.engine.dialect.identifier_preparer.quote_identifier(
            identifier
        )

    def _qualified_table(self, catalog: str, database: str, table: str) -> str:
        """构造完整表标识符。"""
        return ".".join(
            self._quote_identifier(part) for part in (catalog, database, table)
        )

    async def _execute(self, sql: str, parameters: tuple[object, ...] = ()) -> None:
        """执行方言引用的管理语句，字符串值交由驱动参数转义。"""
        async with self._provider.engine.connect() as connection:
            await connection.exec_driver_sql(sql, parameters)

    async def _grant_workload_group_usage(
        self,
        *,
        role: str,
        workload_group: str,
    ) -> None:
        """向角色授予工作组使用权限并识别工作组删除竞争。"""
        group = self._quote_identifier(workload_group)
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
        """创建角色并识别 Doris 角色名冲突。"""
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
        role_name: str,
    ) -> None:
        """创建查询用户并识别用户名冲突。"""
        try:
            await self._execute(
                "CREATE USER %s@%s IDENTIFIED BY %s DEFAULT ROLE %s",
                (query_user, "%", password, role_name),
            )
        except OperationalError as exc:
            message = str(exc.orig).casefold()
            if re.search(r"\buser\b.+\balready exists?\b", message):
                raise DorisQueryUserAlreadyExistsError(query_user) from exc
            raise

    def _select_privilege(self, columns: Sequence[str]) -> str:
        """构造表级或列级 SELECT 权限表达式。"""
        if not columns:
            return "SELECT_PRIV"
        quoted_columns = ",".join(self._quote_identifier(column) for column in columns)
        return f"SELECT_PRIV({quoted_columns})"


def _row_policy_from_row(row: Mapping[str, object]) -> DorisRowPolicy:
    """将 Doris SHOW ROW POLICY 结果转换为稳定模型。"""
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
    """从不同 Doris 小版本的 SHOW ROLES 结果读取角色名。"""
    for key in ("Name", "Role", "RoleName"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


def role_users_from_row(row: Mapping[str, object]) -> tuple[str, ...]:
    """从 SHOW ROLES 结果读取关联的 Doris 用户身份。"""
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
