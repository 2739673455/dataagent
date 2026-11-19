"""PostgreSQL 17 上的原生角色、ACL 和 RLS 行为。"""

import asyncio
import os
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.identity.errors import (
    BusinessPgQueryUserAlreadyExistsError,
    InvalidBusinessPgPermissionError,
)
from app.identity.repositories.business_pg_role import BusinessPgRoleRepository
from app.shared.clients.business_pg_client_manager import BusinessPgClientManager
from app.shared.config.app_config import BusinessPgConfig


@pytest.mark.skipif(
    not os.getenv("DATAAGENT_TEST_PG_DSN"), reason="requires disposable PostgreSQL"
)
def test_native_grants_policies_and_role_lifecycle():
    async def run():
        dsn = make_url(os.environ["DATAAGENT_TEST_PG_DSN"])
        assert (
            dsn.database is not None
            and dsn.host is not None
            and dsn.username is not None
        )
        db = dsn.database
        prefix = "da_test_" + uuid4().hex[:8]
        schema, role, login = prefix + "_s", prefix + "_r", prefix + "_u"
        other, other_login = prefix + "_r2", prefix + "_u2"
        password = uuid4().hex
        superadmin = BusinessPgClientManager(
            BusinessPgConfig(
                host=dsn.host,
                port=dsn.port or 5432,
                user=dsn.username,
                password=SecretStr(dsn.password or "unused"),
                database=db,
                schema_name=schema,
            )
        )
        admin_name = prefix + "_m"
        admin = BusinessPgClientManager(
            BusinessPgConfig(
                host=dsn.host,
                port=dsn.port or 5432,
                user=admin_name,
                password=SecretStr(password),
                database=db,
                schema_name=schema,
            )
        )
        repository = BusinessPgRoleRepository(admin, database=db, schema=schema)
        target = {"catalog": schema, "database": db}
        grants = {"role_name": role, **target}

        async def observe(r=role, u=login):
            return await repository.read_authorization(
                role_name=r,
                query_user=u,
                data_source="business_pg",
                **target,
            )

        async def query(sql, user=login):
            engine = create_async_engine(
                URL.create(
                    "postgresql+psycopg",
                    username=user,
                    password=password,
                    host=dsn.host,
                    port=dsn.port,
                    database=db,
                )
            )
            try:
                async with engine.connect() as connection:
                    result = await connection.exec_driver_sql(sql)
                    return result.scalars().all()
            finally:
                await engine.dispose()

        async def apply(
            policy_name,
            predicate,
            policy_type: Literal["PERMISSIVE", "RESTRICTIVE"] = "PERMISSIVE",
        ):
            await repository.create_row_policy(
                policy_name=policy_name,
                **grants,
                table="orders",
                policy_type=policy_type,
                predicate_sql=predicate,
            )

        try:
            async with superadmin.engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"CREATE ROLE {admin_name} LOGIN CREATEROLE NOSUPERUSER PASSWORD '{password}'"
                )
                await connection.exec_driver_sql(
                    f"GRANT CREATE ON DATABASE {db} TO {admin_name}"
                )
                await connection.exec_driver_sql(
                    f"GRANT CONNECT ON DATABASE {db} TO {admin_name} WITH GRANT OPTION"
                )
            async with admin.engine.begin() as connection:
                for ddl in (
                    f"CREATE SCHEMA {schema}",
                    f"CREATE TABLE {schema}.orders (id integer, region text)",
                    f"INSERT INTO {schema}.orders VALUES (1, 'east'), (2, 'west'), (3, 'east')",
                    f"CREATE TABLE {schema}.other (id integer) PARTITION BY RANGE (id)",
                    f"CREATE TABLE {schema}.other_child PARTITION OF {schema}.other FOR VALUES FROM (0) TO (10)",
                    f"CREATE VIEW {schema}.owner_view AS SELECT * FROM {schema}.orders",
                    f"CREATE TABLE {schema}.external_rls (id integer)",
                    f"INSERT INTO {schema}.external_rls VALUES (1)",
                    f"ALTER TABLE {schema}.external_rls ENABLE ROW LEVEL SECURITY",
                ):
                    await connection.exec_driver_sql(ddl)

            await repository.create_role_identity(
                role_name=role,
                query_user=login,
                password=password,
                workload_group="standard",
            )
            await repository.create_role_identity(
                role_name=other,
                query_user=other_login,
                password=password,
                workload_group="reporting",
            )
            with pytest.raises(BusinessPgQueryUserAlreadyExistsError):
                await repository.create_role_identity(
                    role_name=prefix + "_aborted",
                    query_user=login,
                    password=password,
                    workload_group="standard",
                )
            assert prefix + "_aborted" not in {
                row["name"] for row in await repository.list_roles()
            }
            assert (await observe()).grants == ()
            assert await query("SHOW default_transaction_read_only") == ["on"]
            assert await query("SHOW work_mem") == ["4MB"]
            assert await query("SHOW work_mem", other_login) == ["16MB"]

            await repository.grant_select(**grants, table="orders", columns=("id",))
            await repository.grant_select(
                role_name=other,
                **target,
                table="orders",
                columns=(),
            )
            async with admin.engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"GRANT INSERT (id) ON {schema}.orders TO {other}"
                )
            with pytest.raises(InvalidBusinessPgPermissionError, match="非所有者"):
                await apply("blocked_writer", "id = 1")
            async with admin.engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"REVOKE INSERT (id) ON {schema}.orders FROM {other}"
                )
            before = await observe()
            assert {(g.table_name, g.column_name) for g in before.grants} == {
                ("orders", "id")
            }
            assert await query(f"SELECT id FROM {schema}.orders ORDER BY id") == [
                1,
                2,
                3,
            ]
            with pytest.raises(DBAPIError) as denied:
                await query(f"SELECT region FROM {schema}.orders")
            assert getattr(denied.value.orig, "sqlstate", None) == "42501"
            assert {row["name"] for row in await repository.list_roles()} >= {
                role,
                other,
            }
            assert await repository.list_workload_groups() == ("standard", "reporting")

            await apply("east", "region = 'east'")
            enabled = await observe()
            assert enabled.fingerprint != before.fingerprint
            assert enabled.row_restricted_tables == frozenset(
                {"orders", "external_rls"}
            )
            assert await query(f"SELECT id FROM {schema}.orders ORDER BY id") == [1, 3]
            assert await query(
                f"SELECT id FROM {schema}.orders ORDER BY id", other_login
            ) == [1, 2, 3]
            await apply("last_two", "id >= 2", "RESTRICTIVE")
            assert await query(f"SELECT id FROM {schema}.orders ORDER BY id") == [3]
            await repository.drop_row_policy(
                policy_name="east",
                **grants,
                table="orders",
            )
            assert await query(f"SELECT id FROM {schema}.orders ORDER BY id") == [2, 3]

            async with admin.engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"CREATE POLICY later_external ON {schema}.orders FOR SELECT TO {role} USING (id = 2)"
                )
            assert await query(f"SELECT id FROM {schema}.orders ORDER BY id") == [2]
            assert await query(
                f"SELECT id FROM {schema}.orders ORDER BY id", other_login
            ) == [1, 2, 3]
            with pytest.raises(InvalidBusinessPgPermissionError):
                await repository.drop_row_policy(
                    policy_name="later_external",
                    **grants,
                    table="orders",
                )
            async with admin.engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"DROP POLICY later_external ON {schema}.orders"
                )

            await repository.grant_select(**grants, table=None, columns=())
            assert {g.table_name for g in (await observe()).grants} == {
                "orders",
                "other",
                "external_rls",
            }
            assert await query(f"SELECT id FROM {schema}.external_rls") == []
            async with admin.engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"CREATE TABLE {schema}.future (id integer)"
                )
            assert "future" not in {g.table_name for g in (await observe()).grants}
            await repository.revoke_select(**grants, table=None, columns=())
            assert (await observe()).grants == ()

            async with superadmin.engine.begin() as connection:
                await connection.exec_driver_sql(f"GRANT pg_read_all_data TO {login}")
            with pytest.raises(InvalidBusinessPgPermissionError):
                await observe()
            async with superadmin.engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"REVOKE pg_read_all_data FROM {login}"
                )

            await repository.drop_role_identity(role_name=role, query_user=login)
            await repository.drop_role_identity(role_name=role, query_user=login)
            assert all(row["name"] != role for row in await repository.list_roles())
            assert await query(
                f"SELECT id FROM {schema}.orders ORDER BY id", other_login
            ) == [1, 2, 3]
            await repository.drop_role_identity(role_name=other, query_user=other_login)
        finally:
            async with superadmin.engine.begin() as connection:
                # Scope cleanup to this test's randomly created objects.
                await connection.exec_driver_sql(
                    f"DROP SCHEMA IF EXISTS {schema} CASCADE"
                )
                for name in (login, other_login, role, other, admin_name):
                    result = await connection.exec_driver_sql(
                        f"SELECT 1 FROM pg_roles WHERE rolname = '{name}'"
                    )
                    if result.scalar():
                        await connection.exec_driver_sql(
                            f"REVOKE CREATE, CONNECT ON DATABASE {db} FROM {name}"
                        )
                    await connection.exec_driver_sql(f"DROP ROLE IF EXISTS {name}")
            await admin.close()
            await superadmin.close()

    asyncio.run(run())
