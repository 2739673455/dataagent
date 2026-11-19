"""使用临时 schema 验证业务 PostgreSQL 元数据与水位扫描。"""

import asyncio
import json
import os
from datetime import datetime, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.metadata.models.catalog import serialize_column_examples
from app.metadata.repositories.source_business_pg import SourceBusinessPgRepo


def test_postgresql_source_catalog_and_cursor_windows():
    dsn = os.getenv("DATAAGENT_TEST_PG_DSN")
    if not dsn:
        pytest.skip("设置 DATAAGENT_TEST_PG_DSN 以运行实际 PostgreSQL 测试")

    async def run():
        engine = create_async_engine(make_url(dsn).set(drivername="postgresql+psycopg"))
        try:
            async with engine.connect() as connection:
                schema = f"metadata_{uuid4().hex}"
                other_schema = f"metadata_{uuid4().hex}"
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
                await connection.execute(text(f"CREATE SCHEMA {other_schema}"))
                await connection.execute(
                    text(
                        f"""
                        CREATE TABLE {schema}."Order Line" (
                            id integer NOT NULL,
                            tenant bigint NOT NULL,
                            amount numeric(12, 2),
                            payload json,
                            updated_at timestamp(6) without time zone,
                            status varchar(20),
                            PRIMARY KEY (tenant, id)
                        )
                        """
                    )
                )
                await connection.execute(
                    text(f'CREATE TABLE {other_schema}."Order Line" (other text)')
                )
                await connection.execute(
                    text(f"SET LOCAL search_path TO {other_schema}")
                )
                for statement in (
                    f"COMMENT ON TABLE {schema}.\"Order Line\" IS 'Orders; by line'",
                    f"COMMENT ON COLUMN {schema}.\"Order Line\".payload IS 'JSON''s payload'",
                    f'CREATE VIEW {schema}.order_ids AS SELECT id FROM {schema}."Order Line"',
                    f"""
                    INSERT INTO {schema}."Order Line" VALUES
                      (1, 42, 10.00, '{{"sku": 1}}', '2024-01-01', 'old'),
                      (2, 42, 20.50, '{{ "sku" : 1 }}', '2024-01-02', 'new'),
                      (3, 42, 31.25, '{{"sku": 2}}', '2024-01-02', 'new'),
                      (4, 42, 46.25, '{{"sku": 3}}', '2024-01-02', 'updated'),
                      (5, 42, NULL, NULL, NULL, 'no cursor')
                    """,
                ):
                    await connection.execute(text(statement))

                repo = SourceBusinessPgRepo(connection, schema)
                assert await repo.list_tables() == ["Order Line"]
                assert not await repo.table_exists("order_ids")
                assert not await repo.table_exists("not_here")
                assert await repo.get_primary_key_columns("Order Line") == [
                    "tenant",
                    "id",
                ]
                assert await repo.get_primary_key_columns("order_ids") == []
                assert await repo.get_table_comment("Order Line") == "Orders; by line"
                assert (
                    "payload",
                    "json",
                    "JSON's payload",
                ) in await repo.get_column_definitions("Order Line")
                types = await repo.get_column_types("Order Line")
                assert types["amount"] == "numeric(12,2)"
                assert types["status"] == "character varying(20)"
                assert types["updated_at"] == "timestamp(6) without time zone"
                samples = await repo.get_table_columns_sample_values(
                    "Order Line", ["status"], 10
                )
                assert set(samples["status"]) == {"old", "new", "updated", "no cursor"}
                values = await repo.get_column_values("Order Line", "payload", limit=10)
                assert len(values) == 4  # structural JSON equality deduplicates SKU 1
                upper = await repo.get_value_sync_upper_bound(
                    "Order Line", "updated_at"
                )
                assert upper == datetime.fromisoformat("2024-01-02T00:00:00")
                batches = [
                    batch
                    async for batch in repo.iter_changed_column_value_batches(
                        "Order Line",
                        "status",
                        "updated_at",
                        datetime.fromisoformat("2024-01-02T00:00:00"),
                        upper,
                        batch_size=1,
                    )
                ]
                assert sorted(batches) == [["new"], ["updated"]]
                full_batches = [
                    batch
                    async for batch in repo.iter_column_value_batches(
                        "Order Line", "status", batch_size=2
                    )
                ]
                assert all(len(batch) == 2 for batch in full_batches)
                assert {item for batch in full_batches for item in batch} == {
                    "old",
                    "new",
                    "updated",
                    "no cursor",
                }
                # DDL and fixtures above roll back when the connection is closed.
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_metadata_examples_are_json_serializable_for_native_pg_types():
    examples = serialize_column_examples(
        [
            time.fromisoformat("09:30:00"),
            Decimal("32.60"),
            UUID(int=0),
            b"\xff",
            [float("nan")],
        ]
    )
    assert json.dumps(examples, allow_nan=False)
    assert "09:30:00" in examples
    assert 32.6 in examples
    assert "00000000-0000-0000-0000-000000000000" in examples
    assert "ff" in examples
    assert ["NaN"] in examples
