"""Run with DATAAGENT_TEST_PG_DSN pointing to a disposable PostgreSQL database."""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy.exc import DBAPIError

from app.metadata.contracts import QueryCatalogColumn, QueryCatalogSnapshot
from app.query.contracts import QueryBatch, QueryExecutionLimits, QueryExecutionOptions
from app.query.errors import QueryExecutionTimeoutError
from app.query.execution.guard import QueryGuardService
from app.query.repositories.business_pg import BusinessPgQueryRepository
from app.shared.clients.business_pg_client_manager import BusinessPgClientManager
from app.shared.config.app_config import BusinessPgConfig


@pytest.fixture()
def postgres() -> Iterator[tuple[BusinessPgConfig, str]]:
    dsn = os.environ.get("DATAAGENT_TEST_PG_DSN")
    if not dsn:
        pytest.skip("DATAAGENT_TEST_PG_DSN not set")
    ident = uuid4().hex[:12]
    namespace = f"query_test_{ident}"
    login = f"query_reader_{ident}"
    password = uuid4().hex
    schema = sql.Identifier(namespace)
    role = sql.Identifier(login)
    table = sql.Identifier(namespace, "orders")
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                role, sql.Literal(password)
            )
        )
        try:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role)
            )
            admin.execute(
                sql.SQL(
                    "CREATE TABLE {} (id integer PRIMARY KEY, private text)"
                ).format(table)
            )
            admin.execute(
                sql.SQL(
                    "INSERT INTO {} VALUES (1, 'private1'), (2, 'private2')"
                ).format(table)
            )
            admin.execute(sql.SQL("GRANT SELECT (id) ON {} TO {}").format(table, role))
            admin.execute(
                sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table)
            )
            admin.execute(
                sql.SQL("CREATE POLICY sample ON {} TO {} USING (id = 1)").format(
                    table, role
                )
            )
            yield (
                BusinessPgConfig(
                    host=admin.info.host,
                    port=admin.info.port,
                    user=login,
                    password=SecretStr(password),
                    database=admin.info.dbname,
                    schema_name=namespace,
                ),
                namespace,
            )
        finally:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(schema))
            admin.execute(sql.SQL("DROP ROLE {}").format(role))


async def _check_postgresql_stream(postgres: tuple[BusinessPgConfig, str]) -> None:
    db, schema = postgres

    async def catalog() -> QueryCatalogSnapshot:
        return QueryCatalogSnapshot(
            table_names=("orders",),
            columns=(
                QueryCatalogColumn("orders", "id", "integer"),
                QueryCatalogColumn("orders", "private", "text"),
            ),
        )

    guard = QueryGuardService(catalog, current_database=db.database, schema_name=schema)
    manager = BusinessPgClientManager(db)
    repository = BusinessPgQueryRepository(manager, schema_name=schema)

    async def query(query_text: str, *, guarded: bool = True) -> list[QueryBatch]:
        if guarded:
            validated = await guard.check(query_text)
            assert validated.valid, validated.issues
            assert validated.normalized_sql is not None
            query_text = validated.normalized_sql
        batches: AsyncIterator[QueryBatch] = repository.stream(
            query_text,
            QueryExecutionLimits(timeout_seconds=1, work_mem_kb=256),
            QueryExecutionOptions(batch_size=1),
        )
        return [batch async for batch in batches]

    try:
        batches = await query(
            "SELECT id, '50%:ok'::text AS label FROM ORDERS ORDER BY id"
        )
        assert batches == [QueryBatch(("id", "label"), ((1, "50%:ok"),))]
        assert await query("SELECT id FROM orders WHERE id = 2") == [
            QueryBatch(("id",), ())
        ]
        assert await query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'orders'"
        ) == [QueryBatch(("column_name",), (("id",),))]
        with pytest.raises(DBAPIError, match="permission denied"):
            await query("SELECT private FROM orders")
        # Internal transaction configuration is checked without routing these probes through the user SQL guard.
        assert await query(
            "SELECT current_setting('transaction_read_only'), current_setting('work_mem')",
            guarded=False,
        ) == [QueryBatch(("current_setting", "current_setting"), (("on", "256kB"),))]
        with pytest.raises(QueryExecutionTimeoutError):
            await query("SELECT pg_sleep(2)", guarded=False)
        assert await query("SELECT id FROM orders") == [QueryBatch(("id",), ((1,),))]
    finally:
        await manager.close()


def test_postgresql_stream_readonly_roles_rls_and_limits(
    postgres: tuple[BusinessPgConfig, str],
) -> None:
    asyncio.run(_check_postgresql_stream(postgres))
