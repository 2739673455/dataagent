import asyncio
import unittest
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from sqlalchemy.ext.asyncio import AsyncConnection

from app.query.models import QueryExecutionLimits
from app.query.repositories.doris import (
    DorisConnectionProvider,
    DorisQueryRepository,
    DorisReadonlyPrivilegeError,
)


class FakeStreamResult:
    def __init__(self) -> None:
        self.closed = False

    def keys(self) -> tuple[str, str]:
        return "id", "name"

    async def partitions(self, batch_size: int) -> AsyncIterator[list[tuple[Any, ...]]]:
        if batch_size != 2:
            raise AssertionError("unexpected batch size")
        yield [(1, "A"), (2, "B")]
        yield [(3, "C")]

    async def close(self) -> None:
        self.closed = True


class FakeConnectionProvider:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self.calls = 0

    def connection(self) -> AsyncConnection:
        self.calls += 1
        return self._connection


class DorisQueryRepositoryTest(unittest.IsolatedAsyncioTestCase):
    def test_requires_effective_readonly_grants(self) -> None:
        DorisQueryRepository.require_readonly_grants(
            [
                {
                    "GlobalPrivs": None,
                    "Roles": "dataagent_default",
                    "TablePrivs": "internal.analytics.orders: Select_priv",
                    "WorkloadGroupPrivs": "dataagent_readonly: Usage_priv",
                }
            ],
            "dataagent_default",
        )

        with self.assertRaises(DorisReadonlyPrivilegeError):
            DorisQueryRepository.require_readonly_grants(
                [
                    {
                        "Roles": "dataagent_default",
                        "DatabasePrivs": ("internal.analytics: Select_priv,Load_priv"),
                    }
                ],
                "dataagent_default",
            )

        with self.assertRaisesRegex(
            DorisReadonlyPrivilegeError,
            "预期的唯一角色",
        ):
            DorisQueryRepository.require_readonly_grants(
                [
                    {
                        "Roles": "dataagent_default,finance",
                        "TablePrivs": "internal.analytics.orders: Select_priv",
                    }
                ],
                "dataagent_default",
            )

    async def test_startup_check_requires_target_database_visibility(self) -> None:
        connection = AsyncMock()
        connection.__aenter__.return_value = connection
        grants_result = Mock()
        grants_result.mappings.return_value.all.return_value = [
            {
                "Roles": "dataagent_default",
                "DatabasePrivs": "internal.other_db: Select_priv",
            }
        ]
        databases_result = Mock()
        databases_result.fetchall.return_value = [("other_db",)]
        connection.execute.side_effect = [grants_result, databases_result, None]
        provider = FakeConnectionProvider(cast(AsyncConnection, connection))
        repo = DorisQueryRepository(cast(DorisConnectionProvider, provider))

        with self.assertRaisesRegex(
            DorisReadonlyPrivilegeError,
            "目标数据库",
        ):
            await repo.verify_readonly_access(
                "dataagent_readonly",
                "ecommerce",
                "dataagent_default",
            )

        visibility_call = connection.execute.await_args_list[1]
        self.assertEqual(str(visibility_call.args[0]), "SHOW DATABASES LIKE :database")
        self.assertEqual(visibility_call.args[1], {"database": "ecommerce"})

    async def test_sets_limits_and_streams_server_side_partitions(self) -> None:
        connection = AsyncMock()
        connection.__aenter__.return_value = connection
        result = FakeStreamResult()
        connection.stream.return_value = result
        provider = FakeConnectionProvider(cast(AsyncConnection, connection))
        repo = DorisQueryRepository(cast(DorisConnectionProvider, provider))
        limits = QueryExecutionLimits(
            workload_group="dataagent_readonly",
            timeout_seconds=17,
            memory_limit_bytes=4096,
            max_scan_rows=100,
            max_scan_bytes=4096,
            max_cell_bytes=1024,
            max_rows=10,
            max_output_bytes=1024,
            batch_size=2,
            sample_rows=1,
        )

        batches = [
            batch
            async for batch in repo.stream(
                "SELECT id, ':secret' AS name FROM t",
                limits,
            )
        ]

        self.assertEqual([len(batch.rows) for batch in batches], [2, 1])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(batches[0].column_names, ("id", "name"))
        self.assertTrue(result.closed)
        executed_sql = [
            str(call.args[0]) for call in connection.execute.await_args_list
        ]
        self.assertEqual(
            executed_sql,
            [
                "SET workload_group = 'dataagent_readonly'",
                "SET query_timeout = 17",
                "SET exec_mem_limit = 4096",
                "SET max_allowed_packet = 1024",
            ],
        )
        streamed_sql = str(connection.stream.await_args.args[0])
        self.assertIn("LIMIT 11", streamed_sql)
        self.assertIn("':secret'", streamed_sql)
        self.assertEqual(connection.stream.await_args.args[0]._bindparams, {})
        self.assertEqual(
            connection.stream.await_args.kwargs["execution_options"],
            {"stream_results": True, "yield_per": 2},
        )

    async def test_empty_result_still_yields_column_metadata(self) -> None:
        connection = AsyncMock()
        connection.__aenter__.return_value = connection
        result = FakeStreamResult()

        async def no_partitions(_: int) -> AsyncIterator[list[tuple[Any, ...]]]:
            if False:
                yield []

        result.partitions = no_partitions  # type: ignore[method-assign]
        connection.stream.return_value = result
        provider = FakeConnectionProvider(cast(AsyncConnection, connection))
        repo = DorisQueryRepository(cast(DorisConnectionProvider, provider))
        limits = QueryExecutionLimits(
            workload_group="dataagent_readonly",
            timeout_seconds=1,
            memory_limit_bytes=1,
            max_scan_rows=1,
            max_scan_bytes=1,
            max_cell_bytes=1,
            max_rows=1,
            max_output_bytes=1024,
            batch_size=1,
        )

        batches = [batch async for batch in repo.stream("SELECT 1", limits)]

        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].column_names, ("id", "name"))
        self.assertEqual(batches[0].rows, ())

    async def test_explain_compiles_guarded_query_before_streaming(self) -> None:
        connection = AsyncMock()
        connection.__aenter__.return_value = connection
        plan_result = Mock()
        plan_result.fetchall.return_value = [("PLAN FRAGMENT 0",), ("SCAN orders",)]
        connection.execute.side_effect = [None, None, None, None, plan_result]
        provider = FakeConnectionProvider(cast(AsyncConnection, connection))
        repo = DorisQueryRepository(cast(DorisConnectionProvider, provider))
        limits = QueryExecutionLimits(
            workload_group="dataagent_readonly",
            timeout_seconds=3,
            memory_limit_bytes=2048,
            max_scan_rows=100,
            max_scan_bytes=4096,
            max_cell_bytes=1024,
            max_rows=5,
            max_output_bytes=1024,
            batch_size=2,
        )

        plan = await repo.explain("SELECT ':secret' AS token FROM orders", limits)

        self.assertEqual(plan, ("PLAN FRAGMENT 0", "SCAN orders"))
        self.assertEqual(provider.calls, 1)
        explain_sql = str(connection.execute.await_args_list[4].args[0])
        self.assertEqual(
            explain_sql,
            "EXPLAIN SELECT ':secret' AS token FROM orders",
        )
        self.assertEqual(connection.execute.await_args_list[4].args[0]._bindparams, {})

    async def test_cancelled_explain_invalidates_connection(self) -> None:
        connection = AsyncMock()
        connection.__aenter__.return_value = connection
        calls = 0

        async def execute(_: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 5:
                await asyncio.Event().wait()

        connection.execute.side_effect = execute
        provider = FakeConnectionProvider(cast(AsyncConnection, connection))
        repo = DorisQueryRepository(cast(DorisConnectionProvider, provider))
        limits = QueryExecutionLimits(
            workload_group="dataagent_readonly",
            timeout_seconds=3,
            memory_limit_bytes=2048,
            max_scan_rows=100,
            max_scan_bytes=4096,
            max_cell_bytes=1024,
            max_rows=5,
            max_output_bytes=1024,
            batch_size=2,
        )
        task = asyncio.create_task(repo.explain("SELECT 1", limits))
        while calls < 5:
            await asyncio.sleep(0)

        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        connection.invalidate.assert_awaited_once()
