"""Doris 业务数据存储测试"""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.ext.asyncio import AsyncConnection

from app.metadata.repositories.source_doris import SourceDorisRepo


def _connection() -> MagicMock:
    connection = MagicMock(spec=AsyncConnection)
    connection.dialect = mysql_dialect()
    connection.execute = AsyncMock()
    connection.stream_scalars = AsyncMock()
    return connection


class SourceDorisRepoTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_column_types_binds_special_table_name(self) -> None:
        connection = _connection()
        result = MagicMock()
        result.fetchall.return_value = [("订单 金额", "decimal(18,2)")]
        connection.execute.return_value = result
        repo = SourceDorisRepo(cast(AsyncConnection, connection))

        column_types = await repo.get_column_types("订单-明细")

        statement, parameters = connection.execute.await_args.args
        self.assertIn("from information_schema.columns", str(statement))
        self.assertIn(":table_name", str(statement))
        self.assertEqual(parameters, {"table_name": "订单-明细"})
        self.assertEqual(column_types, {"订单 金额": "decimal(18,2)"})

    async def test_get_column_values_quotes_special_identifiers(self) -> None:
        connection = _connection()
        result = MagicMock()
        result.scalars.return_value.fetchall.return_value = [100, 200]
        connection.execute.return_value = result
        repo = SourceDorisRepo(cast(AsyncConnection, connection))

        values = await repo.get_column_values("订单-明细", "2026 销售额", limit=5)

        statement = connection.execute.await_args.args[0]
        self.assertEqual(
            str(statement),
            "select distinct `2026 销售额` from `订单-明细` limit 5",
        )
        self.assertEqual(values, [100, 200])

    async def test_sample_values_reads_special_columns_by_position(self) -> None:
        connection = _connection()
        result = MagicMock()
        result.fetchall.return_value = [("华东", 100), ("华东", 200)]
        connection.execute.return_value = result
        repo = SourceDorisRepo(cast(AsyncConnection, connection))

        values = await repo.get_table_columns_sample_values(
            "订单-明细",
            ["销售/区域", "2026 销售额"],
            limit=5,
        )

        statement = connection.execute.await_args.args[0]
        self.assertEqual(
            str(statement),
            "select `销售/区域`, `2026 销售额` from `订单-明细` limit 5",
        )
        self.assertEqual(
            values,
            {"销售/区域": ["华东"], "2026 销售额": [100, 200]},
        )

    def test_identifier_quoting_escapes_quote_characters(self) -> None:
        connection = _connection()
        repo = SourceDorisRepo(cast(AsyncConnection, connection))

        self.assertEqual(repo._quote_identifier("order`detail"), "`order``detail`")

    async def test_get_value_sync_upper_bound_quotes_identifiers(self) -> None:
        connection = _connection()
        result = MagicMock()
        result.scalar.return_value = 42
        connection.execute.return_value = result
        repo = SourceDorisRepo(cast(AsyncConnection, connection))

        upper_bound = await repo.get_value_sync_upper_bound(
            "订单-明细",
            "更新 批次",
        )

        statement = connection.execute.await_args.args[0]
        self.assertEqual(
            str(statement),
            "select max(`更新 批次`) from `订单-明细`",
        )
        self.assertEqual(upper_bound, 42)

    async def test_changed_value_batches_bind_closed_watermark_window(self) -> None:
        connection = _connection()
        stream_result = MagicMock()

        async def partitions(_: int):
            yield ["已支付", "已完成"]
            yield ["已取消"]

        stream_result.partitions = partitions
        connection.stream_scalars.return_value = stream_result
        repo = SourceDorisRepo(cast(AsyncConnection, connection))

        batches = [
            batch
            async for batch in repo.iter_changed_column_value_batches(
                "订单-明细",
                "订单 状态",
                "更新 批次",
                100,
                200,
                batch_size=2,
            )
        ]

        statement, parameters = connection.stream_scalars.await_args.args[:2]
        self.assertEqual(
            str(statement),
            "select distinct `订单 状态` from `订单-明细` "
            "where `更新 批次` >= :lower_bound "
            "and `更新 批次` <= :upper_bound",
        )
        self.assertEqual(parameters, {"lower_bound": 100, "upper_bound": 200})
        self.assertEqual(batches, [["已支付", "已完成"], ["已取消"]])
