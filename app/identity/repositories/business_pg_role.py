"""PostgreSQL 业务角色、对象权限及原生行级安全管理。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import sqlglot
from psycopg import AsyncConnection as PsycopgConnection
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlglot import exp
from sqlglot.errors import ParseError

from app.identity.errors import (
    BusinessPgQueryUserAlreadyExistsError,
    BusinessPgRoleAlreadyExistsError,
    BusinessPgWorkloadGroupNotFoundError,
    InvalidBusinessPgPermissionError,
)
from app.identity.models.business_pg import (
    BusinessPgAuthorizationSnapshot,
    BusinessPgRowPolicy,
    BusinessPgSelectGrant,
)
from app.shared.clients.business_pg_client_manager import BusinessPgClientManager
from app.shared.contracts.business_pg import (
    BUSINESS_PG_RESOURCE_PROFILES,
    validate_business_pg_identifier,
)

_BASELINE_POLICY = "dataagent_managed_rls_baseline"
_POLICY_MARKER = "DataAgent managed row policy"
_RELATIONS = "c.relkind IN ('r', 'p') AND NOT c.relispartition"
_POLICIES = """
    SELECT c.relname AS table_name, c.relrowsecurity, c.relforcerowsecurity,
           p.polname AS policy_name, p.polpermissive, p.polcmd,
           obj_description(p.oid, 'pg_policy') AS comment,
           pg_get_expr(p.polqual, p.polrelid) AS predicate,
           p.polroles
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_policy p ON p.polrelid = c.oid
    WHERE n.nspname = :schema
"""


class BusinessPgRoleRepository:
    """仅通过业务库管理连接创建角色和修改配置 schema 的 ACL。"""

    def __init__(
        self, provider: BusinessPgClientManager, *, database: str, schema: str
    ) -> None:
        self._provider = provider
        self._database = database
        self._schema = schema
        self._role_marker = f"DataAgent managed role: {database}/{schema}"

    async def list_roles(self) -> list[dict[str, Any]]:
        """列出 PostgreSQL 非内置分组角色及其成员。"""
        async with self._provider.engine.connect() as connection:
            result = await connection.execute(
                text("""
                    SELECT r.rolname::text AS name, ARRAY(
                        SELECT u.rolname::text
                        FROM pg_auth_members m JOIN pg_roles u ON u.oid = m.member
                        WHERE m.roleid = r.oid ORDER BY u.rolname
                    ) AS users
                    FROM pg_roles r
                    WHERE NOT r.rolcanlogin AND left(r.rolname, 3) <> 'pg_'
                    ORDER BY r.rolname
                """)
            )
            return [dict(row) for row in result.mappings()]

    async def read_authorization(
        self,
        *,
        role_name: str,
        query_user: str,
        data_source: str,
        catalog: str,
        database: str,
    ) -> BusinessPgAuthorizationSnapshot:
        """观察同一快照下的独立查询账号、对象 ACL 和 SELECT 行级策略。"""
        self._check_scope(catalog, database)
        params = {
            "schema": self._schema,
            "role": role_name,
            "login": query_user,
            "marker": self._role_marker,
            "database": self._database,
        }
        async with self._provider.engine.begin() as connection:
            await connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            identity = await connection.execute(
                text("""
                    SELECT pg_has_role(u.oid, r.oid, 'USAGE')
                       AND shobj_description(r.oid, 'pg_authid') = :marker
                       AND current_database() = :database
                       AND has_database_privilege(u.oid, current_database(), 'CONNECT')
                       AND NOT has_database_privilege(u.oid, current_database(), 'CREATE')
                       AND NOT has_schema_privilege(u.oid, :schema, 'CREATE')
                       AND d.datdba NOT IN (r.oid, u.oid)
                       AND ARRAY(SELECT DISTINCT m.roleid FROM pg_auth_members m
                                 WHERE m.member = u.oid)::oid[] = ARRAY[r.oid]::oid[]
                       AND NOT EXISTS (SELECT 1 FROM pg_auth_members m
                                       WHERE m.member = r.oid
                                          OR (m.member = u.oid AND m.admin_option))
                       AND NOT EXISTS (
                           SELECT 1 FROM pg_class c JOIN pg_namespace n
                               ON n.oid = c.relnamespace
                           WHERE n.nspname = :schema AND c.relowner IN (r.oid, u.oid)
                       ) AS valid,
                       has_schema_privilege(u.oid, :schema, 'USAGE') AS schema_usage
                    FROM pg_roles u CROSS JOIN pg_roles r
                    JOIN pg_database d ON d.datname = current_database()
                    WHERE u.rolname = :login AND r.rolname = :role
                      AND u.rolcanlogin AND NOT r.rolcanlogin
                      AND NOT (u.rolsuper OR u.rolcreaterole OR u.rolcreatedb
                               OR u.rolreplication OR u.rolbypassrls)
                      AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb
                               OR r.rolreplication OR r.rolbypassrls)
                """),
                params,
            )
            row = identity.mappings().one_or_none()
            if row is None or not row["valid"]:
                raise InvalidBusinessPgPermissionError(
                    detail="PostgreSQL 查询账号或角色超出安全授权范围，或与平台绑定不一致"
                )
            assets = await connection.execute(
                text(f"""
                    SELECT c.relname::text AS table_name, a.attname::text AS column_name,
                           c.relrowsecurity, c.relforcerowsecurity,
                           has_table_privilege(u.oid, c.oid, 'SELECT') AS table_select,
                           has_column_privilege(u.oid, c.oid, a.attnum, 'SELECT') AS column_select
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_roles u ON u.rolname = :login
                    LEFT JOIN pg_attribute a ON a.attrelid = c.oid
                       AND a.attnum > 0 AND NOT a.attisdropped
                    WHERE n.nspname = :schema AND {_RELATIONS}
                """),
                params,
            )
            restricted: dict[str, bool] = {}
            grants: set[tuple[str, str | None]] = set()
            for asset in assets.mappings():
                table = asset["table_name"]
                if asset["relrowsecurity"]:
                    restricted[table] = asset["relforcerowsecurity"]
                if not row["schema_usage"]:
                    continue
                if asset["table_select"]:
                    grants.add((table, None))
                elif asset["column_select"]:
                    grants.add((table, asset["column_name"]))
            policies = await connection.execute(
                text(
                    _POLICIES
                    + """
                    AND c.relrowsecurity AND p.polcmd IN ('r', '*')
                    AND EXISTS (
                        SELECT 1 FROM unnest(p.polroles) AS policy_role(id)
                        WHERE CASE WHEN id = 0 THEN true
                            ELSE pg_has_role(cast(:login AS name), id, 'USAGE') END
                    )
                    """
                ),
                params,
            )
            effective_rules = {
                (
                    policy["table_name"],
                    policy["polpermissive"],
                    policy["predicate"] or "true",
                )
                for policy in policies.mappings()
                if policy["table_name"] in restricted
            }

        ordered_grants = sorted(grants, key=lambda item: (item[0], item[1] or ""))
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    role_name,
                    query_user,
                    data_source,
                    catalog,
                    database,
                    ordered_grants,
                    restricted,
                    sorted(effective_rules),
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return BusinessPgAuthorizationSnapshot(
            grants=tuple(
                BusinessPgSelectGrant(role_name, data_source, database, table, column)
                for table, column in ordered_grants
            ),
            fingerprint=fingerprint,
            row_restricted_tables=frozenset(restricted),
        )

    async def list_workload_groups(self) -> tuple[str, ...]:
        return tuple(BUSINESS_PG_RESOURCE_PROFILES)

    async def workload_group_exists(self, workload_group: str) -> bool:
        return workload_group in BUSINESS_PG_RESOURCE_PROFILES

    async def create_role_identity(
        self,
        *,
        role_name: str,
        query_user: str,
        password: str,
        workload_group: str,
    ) -> None:
        """在同一 PostgreSQL 事务创建 NOLOGIN 分组与最小权限登录身份。"""
        validate_business_pg_identifier(role_name)
        validate_business_pg_identifier(query_user)
        profile = BUSINESS_PG_RESOURCE_PROFILES.get(workload_group)
        if profile is None:
            raise BusinessPgWorkloadGroupNotFoundError(workload_group)
        role, login = map(self._quote, (role_name, query_user))
        database = self._quote(self._database)
        schema = self._quote(self._schema)
        attributes = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        async with self._provider.engine.begin() as connection:
            try:
                await connection.exec_driver_sql(
                    f"CREATE ROLE {role} NOLOGIN {attributes}"
                )
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) == "42710":
                    raise BusinessPgRoleAlreadyExistsError(role_name) from None
                raise
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            if not isinstance(driver, PsycopgConnection):
                raise TypeError("查询身份需要 psycopg PostgreSQL 连接")
            # 仅将 libpq 生成的 SCRAM 校验值交给不支持绑定的角色 DDL；
            # 明文密码不进入 SQL 日志，失败时也不向外传播校验值。
            verifier = driver.pgconn.encrypt_password(
                password.encode(), query_user.encode(), b"scram-sha-256"
            ).decode("ascii")
            try:
                await connection.exec_driver_sql(
                    f"CREATE ROLE {login} LOGIN INHERIT {attributes} "
                    f"CONNECTION LIMIT {profile.connection_limit} PASSWORD {self._literal(verifier)}"
                )
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) == "42710":
                    raise BusinessPgQueryUserAlreadyExistsError(query_user) from None
                raise RuntimeError("无法创建 PostgreSQL 查询登录角色") from None
            await connection.exec_driver_sql(
                f"COMMENT ON ROLE {role} IS {self._literal(self._role_marker)}"
            )
            await connection.exec_driver_sql(f"GRANT {role} TO {login}")
            await connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE {database} TO {role}"
            )
            await connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA {schema} TO {role}"
            )
            for setting, value in (
                ("default_transaction_read_only", "on"),
                ("statement_timeout", f"{profile.statement_timeout_seconds}s"),
                ("work_mem", f"{profile.work_mem_kib}kB"),
            ):
                await connection.exec_driver_sql(
                    f"ALTER ROLE {login} IN DATABASE {database} SET {setting} "
                    f"TO {self._literal(value)}"
                )

    async def drop_role_identity(self, *, role_name: str, query_user: str) -> None:
        """删除应用拥有的角色与策略；若存在外部依赖则回滚，不级联删除对象。"""
        role, login = map(self._quote, (role_name, query_user))
        async with self._provider.engine.begin() as connection:
            # 跨 auth、业务库不存在分布式事务；支持业务事务已经成功的重试。
            result = await connection.execute(
                text("""
                    SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = :role
                """),
                {"role": role_name},
            )
            existing = result.one_or_none()
            if existing is None:
                query_exists = await connection.scalar(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :login"),
                    {"login": query_user},
                )
                if query_exists:
                    raise InvalidBusinessPgPermissionError(
                        detail="查询账号仍存在，无法验证托管绑定"
                    )
                return
            if existing[0] != self._role_marker:
                raise InvalidBusinessPgPermissionError(
                    detail="拒绝删除非本数据源托管的 PostgreSQL 角色"
                )
            members = await connection.execute(
                text("""
                    SELECT u.rolname::text FROM pg_roles r
                    JOIN pg_auth_members m ON m.roleid = r.oid
                    JOIN pg_roles u ON u.oid = m.member
                    WHERE r.rolname = :role
                      AND NOT (u.rolname = current_user AND m.admin_option
                               AND NOT m.inherit_option AND NOT m.set_option)
                    ORDER BY u.rolname
                """),
                {"role": role_name},
            )
            if set(members.scalars()) != {query_user}:
                raise InvalidBusinessPgPermissionError(
                    detail="查询账号成员关系已变更，请先核对数据库角色"
                )
            policies = await self._owned_policies(connection, role_name)
            for policy in policies:
                if not policy.managed:
                    raise InvalidBusinessPgPermissionError(
                        detail="角色仍有外部行级策略，请先在 PostgreSQL 管理端解除绑定"
                    )
                await connection.exec_driver_sql(
                    f"DROP POLICY {self._quote(policy.policy_name)} ON {self._target(policy.table_name)}"
                )
            await self._mutate_select(
                connection,
                False,
                role_name=role_name,
                table=None,
                columns=(),
            )
            await connection.exec_driver_sql(f"DROP ROLE {login}")
            await connection.exec_driver_sql(
                f"REVOKE CONNECT ON DATABASE {self._quote(self._database)} FROM {role}"
            )
            await connection.exec_driver_sql(
                f"REVOKE USAGE ON SCHEMA {self._quote(self._schema)} FROM {role}"
            )
            # DROP ROLE 会自动从声明多个角色的策略中移除此角色；
            # 删除最后一个匹配角色不是 PUBLIC 回退，不依赖该行为：先同步应用基线。
            for table in await self._relations(connection):
                await self._sync_baseline(connection, table, omit_role=role_name)
            await connection.exec_driver_sql(f"DROP ROLE {role}")

    async def list_role_row_policies(self, role_name: str) -> list[BusinessPgRowPolicy]:
        async with self._provider.engine.connect() as connection:
            return await self._owned_policies(connection, role_name)

    async def _owned_policies(
        self,
        connection: AsyncConnection,
        role_name: str,
    ) -> list[BusinessPgRowPolicy]:
        result = await connection.execute(
            text(
                _POLICIES
                + """
                AND p.polroles = ARRAY[(SELECT oid FROM pg_roles WHERE rolname = :role)]::oid[]
                AND p.polcmd IN ('r', '*') AND p.polname <> :baseline
                ORDER BY c.relname, p.polname
                """
            ),
            {"schema": self._schema, "role": role_name, "baseline": _BASELINE_POLICY},
        )
        return [
            BusinessPgRowPolicy(
                policy_name=str(row["policy_name"]),
                catalog_name=self._schema,
                database_name=self._database,
                table_name=str(row["table_name"]),
                policy_type="PERMISSIVE" if row["polpermissive"] else "RESTRICTIVE",
                predicate=str(row["predicate"] or "true"),
                managed=row["comment"] == _POLICY_MARKER,
            )
            for row in result.mappings()
        ]

    async def list_table_columns(self, database: str, table: str) -> tuple[str, ...]:
        self._check_scope(self._schema, database)
        async with self._provider.engine.connect() as connection:
            return await self._columns(connection, table)

    async def grant_select(
        self,
        *,
        role_name: str,
        catalog: str,
        database: str,
        table: str | None,
        columns: Sequence[str],
    ) -> None:
        self._check_scope(catalog, database)
        async with self._provider.engine.begin() as connection:
            await self._mutate_select(
                connection,
                True,
                role_name=role_name,
                table=table,
                columns=columns,
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
        self._check_scope(catalog, database)
        async with self._provider.engine.begin() as connection:
            await self._mutate_select(
                connection,
                False,
                role_name=role_name,
                table=table,
                columns=columns,
            )

    async def _mutate_select(
        self,
        connection: AsyncConnection,
        grant: bool,
        *,
        role_name: str,
        table: str | None,
        columns: Sequence[str],
    ) -> None:
        """schema 范围只覆盖现有业务关系，不隐式修改未来表的默认 ACL。"""
        action, destination = ("GRANT", "TO") if grant else ("REVOKE", "FROM")
        role = self._quote(role_name)
        tables = (table,) if table is not None else await self._relations(connection)
        if table is None and columns:
            raise InvalidBusinessPgPermissionError(
                detail="列级授权必须指定对应的数据表"
            )
        for name in tables:
            actual = await self._columns(connection, name)
            if not actual:
                if not grant:
                    continue
                raise InvalidBusinessPgPermissionError(
                    detail="目标表不存在或不在业务范围内"
                )
            if not set(columns).issubset(actual):
                raise InvalidBusinessPgPermissionError(detail="目标列不存在或已删除")
            # 整表撤回也清除该角色单独持有的列级 ACL。
            selections = [tuple(columns)]
            if not grant and not columns:
                selections.append(actual)
            for selected in selections:
                privilege = "SELECT"
                if selected:
                    privilege += " (" + ", ".join(map(self._quote, selected)) + ")"
                await connection.exec_driver_sql(
                    f"{action} {privilege} ON TABLE {self._target(name)} {destination} {role}"
                )
            if grant:
                await self._sync_baseline(connection, name)

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
        self._check_scope(catalog, database)
        self._validate_policy_name(policy_name)
        if policy_type not in {"RESTRICTIVE", "PERMISSIVE"}:
            raise InvalidBusinessPgPermissionError(detail="未知的策略组合方式")
        predicate_sql = validate_policy_predicate(predicate_sql).replace("%", "%%")
        target, policy, role = (
            self._target(table),
            self._quote(policy_name),
            self._quote(role_name),
        )
        async with self._provider.engine.begin() as connection:
            if not await self._columns(connection, table):
                raise InvalidBusinessPgPermissionError(
                    detail="目标表不存在或不在业务范围内"
                )
            await connection.exec_driver_sql(
                f"LOCK TABLE {target} IN ACCESS EXCLUSIVE MODE"
            )
            prior = await connection.execute(
                text(f"""
                    SELECT c.relrowsecurity, EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema AND c.relname = :table AND {_RELATIONS}
                """),
                {"schema": self._schema, "table": table},
            )
            enabled, had_policies = prior.one()
            await connection.exec_driver_sql(
                "SET LOCAL standard_conforming_strings = on"
            )
            try:
                await connection.exec_driver_sql(
                    f"CREATE POLICY {policy} ON {target} AS {policy_type} FOR SELECT "
                    f"TO {role} USING ({predicate_sql})"
                )
            except DBAPIError as exc:
                raise InvalidBusinessPgPermissionError(
                    detail="无法创建 PostgreSQL 行级策略，请检查策略名称、字段和布尔谓词"
                ) from exc
            await connection.exec_driver_sql(
                f"COMMENT ON POLICY {policy} ON {target} IS {self._literal(_POLICY_MARKER)}"
            )
            if not enabled:
                if await self._has_nonowner_writers(connection, table):
                    raise InvalidBusinessPgPermissionError(
                        detail="该表有非所有者写入权限，请先由数据库管理员配置写入策略并启用 RLS"
                    )
                await connection.exec_driver_sql(
                    f"ALTER TABLE {target} ENABLE ROW LEVEL SECURITY"
                )
                if not had_policies:
                    # 初始空基线不会扩大任何角色的权限。
                    admin = await connection.scalar(text("SELECT current_user::text"))
                    await connection.exec_driver_sql(
                        f"CREATE POLICY {_BASELINE_POLICY} ON {target} FOR SELECT "
                        f"TO {self._quote(str(admin))} USING (false)"
                    )
                    await connection.exec_driver_sql(
                        f"COMMENT ON POLICY {_BASELINE_POLICY} ON {target} IS "
                        f"{self._literal(self._role_marker)}"
                    )
            await self._sync_baseline(connection, table)

    async def drop_row_policy(
        self,
        *,
        policy_name: str,
        role_name: str,
        catalog: str,
        database: str,
        table: str,
    ) -> None:
        self._check_scope(catalog, database)
        self._validate_policy_name(policy_name)
        async with self._provider.engine.begin() as connection:
            matching = [
                p
                for p in await self._owned_policies(connection, role_name)
                if p.policy_name == policy_name and p.table_name == table
            ]
            if not matching or not matching[0].managed:
                raise InvalidBusinessPgPermissionError(
                    detail="只能删除当前角色绑定的托管行级策略"
                )
            await connection.exec_driver_sql(
                f"DROP POLICY {self._quote(policy_name)} ON {self._target(table)}"
            )
            await self._sync_baseline(connection, table)

    async def _sync_baseline(
        self,
        connection: AsyncConnection,
        table: str,
        *,
        omit_role: str | None = None,
    ) -> None:
        """仅维护应用首次开启 RLS 时创建的基线，已有外部 RLS 从不自动放行。"""
        params = {
            "schema": self._schema,
            "table": table,
            "marker": self._role_marker,
            "policy": _BASELINE_POLICY,
            "omit": omit_role,
        }
        has_baseline = await connection.scalar(
            text("""
                SELECT true
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_policy p ON p.polrelid = c.oid
                WHERE n.nspname = :schema AND c.relname = :table AND p.polname = :policy
                  AND obj_description(p.oid, 'pg_policy') = :marker
            """),
            params,
        )
        if not has_baseline:
            return
        result = await connection.execute(
            text("""
                SELECT r.rolname::text
                FROM pg_roles r CROSS JOIN pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema AND c.relname = :table
                  AND r.rolname::text IS DISTINCT FROM cast(:omit AS text)
                  AND shobj_description(r.oid, 'pg_authid') = :marker
                  AND has_any_column_privilege(r.oid, c.oid, 'SELECT')
                ORDER BY r.rolname
            """),
            params,
        )
        active = list(result.scalars())
        if active:
            roles = ", ".join(map(self._quote, active))
        else:
            admin = await connection.scalar(text("SELECT current_user::text"))
            roles = self._quote(str(admin))
        # 查询计划可一次性计算此非相关子查询。即使任意原生 permissive
        # 策略在之后被管理员创建，基线也不会与它通过 OR 合并而放宽访问。
        relation = self._literal(sql.Identifier(self._schema, table).as_string())
        predicate = (
            f"""NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_policy p
            CROSS JOIN pg_catalog.unnest(p.polroles) AS policy_role(id)
            WHERE p.polrelid = {relation}::pg_catalog.regclass
              AND p.polname <> '{_BASELINE_POLICY}'
              AND p.polpermissive AND p.polcmd IN ('r', '*')
              AND CASE WHEN id = 0 THEN true
                  ELSE pg_catalog.pg_has_role(CURRENT_USER, id, 'USAGE') END
        )"""
            if active
            else "false"
        )
        await connection.exec_driver_sql(
            f"ALTER POLICY {_BASELINE_POLICY} ON {self._target(table)} TO {roles} USING ({predicate})"
        )

    async def _has_nonowner_writers(
        self, connection: AsyncConnection, table: str
    ) -> bool:
        """拒绝为外部非所有者写入者自动切换 RLS。"""
        return bool(
            await connection.scalar(
                text("""
                WITH target AS (
                    SELECT c.oid, c.relacl, c.relowner
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema AND c.relname = :table
                ), permissions AS (
                    SELECT t.relowner, acl.grantee, acl.privilege_type
                    FROM target t, aclexplode(t.relacl) acl
                    UNION ALL
                    SELECT t.relowner, acl.grantee, acl.privilege_type
                    FROM target t JOIN pg_attribute a ON a.attrelid = t.oid,
                         aclexplode(a.attacl) acl
                )
                SELECT EXISTS (
                    SELECT 1 FROM permissions p LEFT JOIN pg_roles r ON r.oid = p.grantee
                    WHERE p.privilege_type IN ('INSERT', 'UPDATE', 'DELETE')
                      AND p.grantee <> p.relowner
                      AND NOT coalesce(r.rolsuper OR r.rolbypassrls, false)
                )
            """),
                {"schema": self._schema, "table": table},
            )
        )

    async def _relations(self, connection: AsyncConnection) -> tuple[str, ...]:
        result = await connection.execute(
            text(f"""
                SELECT c.relname::text FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema AND {_RELATIONS} ORDER BY c.relname
            """),
            {"schema": self._schema},
        )
        return tuple(result.scalars())

    async def _columns(
        self, connection: AsyncConnection, table: str
    ) -> tuple[str, ...]:
        result = await connection.execute(
            text(f"""
                SELECT a.attname::text FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema AND c.relname = :table AND {_RELATIONS}
                  AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum
            """),
            {"schema": self._schema, "table": table},
        )
        return tuple(result.scalars())

    def _check_scope(self, schema: str, database: str) -> None:
        if (schema, database) != (self._schema, self._database):
            raise InvalidBusinessPgPermissionError(
                detail="操作目标不属于配置的 PostgreSQL 业务范围"
            )

    def _target(self, table: str) -> str:
        return f"{self._quote(self._schema)}.{self._quote(table)}"

    def _quote(self, identifier: str) -> str:
        """引用单个 PostgreSQL 标识符，并处理 psycopg DBAPI 百分号。"""
        if not identifier or len(identifier.encode()) > 63 or "\x00" in identifier:
            raise InvalidBusinessPgPermissionError(
                detail="PostgreSQL 标识符长度或格式无效"
            )
        return self._provider.engine.dialect.identifier_preparer.quote_identifier(
            identifier
        )

    @staticmethod
    def _literal(value: str) -> str:
        return sql.Literal(value).as_string().replace("%", "%%")

    @staticmethod
    def _validate_policy_name(policy_name: str) -> None:
        if policy_name == _BASELINE_POLICY:
            raise InvalidBusinessPgPermissionError(
                detail="该策略名称由 DataAgent 内部保留"
            )


def role_name_from_row(row: Mapping[str, object]) -> str | None:
    value = row.get("name")
    return str(value) if value is not None else None


def role_users_from_row(row: Mapping[str, object]) -> tuple[str, ...]:
    users = row.get("users")
    return tuple(map(str, users)) if isinstance(users, (tuple, list)) else ()


def validate_policy_predicate(
    predicate: str,
) -> str:
    """确认行策略输入是单个 SQL 表达式，并保留原始语义。"""
    normalized = predicate.strip()
    if not normalized:
        raise InvalidBusinessPgPermissionError(detail="行级策略谓词表达式不能为空")
    try:
        statements = sqlglot.parse(normalized, read="postgres")
    except ParseError as exc:
        raise InvalidBusinessPgPermissionError(
            detail="PostgreSQL 行级策略谓词表达式语法无效"
        ) from exc
    # 粒度是单表行过滤；只支持引用未限定列名、值和常用比较/运算，
    # 不把查询、命令、函数调用或原始用户文本插入管理 DDL。
    root = statements[0] if len(statements) == 1 else None
    allowed = (
        exp.And,
        exp.Or,
        exp.Not,
        exp.Paren,
        exp.Column,
        exp.Identifier,
        exp.Literal,
        exp.Null,
        exp.Boolean,
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.Is,
        exp.In,
        exp.Between,
        exp.Like,
        exp.ILike,
        exp.Escape,
        exp.Add,
        exp.Sub,
        exp.Mul,
        exp.Div,
        exp.Mod,
        exp.Neg,
    )
    if root is None or any(
        not isinstance(node, allowed) or (isinstance(node, exp.Column) and node.table)
        for node in root.walk()
    ):
        raise InvalidBusinessPgPermissionError(
            detail="行级策略必须是使用当前表字段、常量、比较及逻辑运算的布尔表达式"
        )
    return root.sql(dialect="postgres", comments=False)
