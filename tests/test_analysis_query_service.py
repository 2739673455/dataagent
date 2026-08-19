import asyncio
import csv
import io
import unittest
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, BinaryIO, cast
from uuid import UUID, uuid4

from app.agents.contracts import AgentSessionKey
from app.models.query import (
    QueryBatch,
    QueryExecutionLimits,
    QueryTableRef,
    QueryValidationIssue,
    QueryValidationResult,
)
from app.repositories.doris_query_repo import DorisQueryRepository
from app.services.analysis_query_service import (
    AnalysisQueryService,
    QueryExecutionTimeoutError,
    QueryOutputLimitExceededError,
    QueryPlanUnavailableError,
    QueryResultLimitExceededError,
    QueryScanLimitExceededError,
    estimate_doris_query_plan,
)
from app.services.query_guard_service import (
    GuardedQuery,
    QueryGuardService,
    QueryRejectedError,
)


class RecordingGuard:
    def __init__(self, *, physical_table: bool = False) -> None:
        self.calls: list[tuple[int, str, str]] = []
        self.physical_table = physical_table

    async def require_safe(
        self,
        user_id: int,
        sql: str,
        dialect: str,
    ) -> GuardedQuery:
        self.calls.append((user_id, sql, dialect))
        validation = QueryValidationResult(
            valid=True,
            dialect="doris",
            normalized_sql="SELECT normalized",
            tables=(
                [QueryTableRef(database="analytics", name="orders")]
                if self.physical_table
                else []
            ),
        )
        return GuardedQuery(sql="SELECT normalized", validation=validation)


class FakeQueryRepo:
    def __init__(
        self,
        batches: list[QueryBatch],
        *,
        plan: tuple[str, ...] = ("PLAN",),
    ) -> None:
        self.batches = batches
        self.plan = plan
        self.sql: str | None = None
        self.explain_sql: str | None = None
        self.closed = False

    async def explain(
        self,
        sql: str,
        limits: QueryExecutionLimits,
    ) -> tuple[str, ...]:
        self.explain_sql = sql
        return self.plan

    async def stream(
        self,
        sql: str,
        limits: QueryExecutionLimits,
    ) -> AsyncGenerator[QueryBatch]:
        self.sql = sql
        try:
            for batch in self.batches:
                yield batch
        finally:
            self.closed = True


class RecordingArtifactStore:
    def __init__(self) -> None:
        self.uploads: list[tuple[int, UUID, str, bytes]] = []

    async def write_artifact(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> None:
        self.uploads.append((user_id, conversation_id, path, content.read()))


class HangingQueryRepo(FakeQueryRepo):
    async def explain(
        self,
        sql: str,
        limits: QueryExecutionLimits,
    ) -> tuple[str, ...]:
        self.explain_sql = sql
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class RejectingGuard:
    async def require_safe(
        self,
        user_id: int,
        sql: str,
        dialect: str,
    ) -> GuardedQuery:
        result = QueryValidationResult(
            valid=False,
            dialect="doris",
            normalized_sql=None,
            issues=[
                QueryValidationIssue(
                    code="readonly_query_required",
                    message="Only SELECT queries are allowed",
                )
            ],
        )
        raise QueryRejectedError(result)


class FailingConnectionProvider:
    def __init__(self) -> None:
        self.calls = 0

    def connection(self) -> object:
        self.calls += 1
        raise AssertionError("Doris connection must not be requested")


def make_limits(**updates: int) -> QueryExecutionLimits:
    values = {
        "workload_group": "dataagent_readonly",
        "timeout_seconds": 10,
        "memory_limit_bytes": 1024,
        "max_scan_rows": 1000,
        "max_scan_bytes": 1024 * 1024,
        "max_cell_bytes": 1024,
        "max_rows": 10,
        "max_output_bytes": 1024,
        "batch_size": 2,
        "sample_rows": 2,
    }
    values.update(updates)
    return QueryExecutionLimits.model_validate(values)


def make_session_key(conversation_id: UUID | None = None) -> AgentSessionKey:
    return AgentSessionKey(
        user_id=9,
        conversation_id=conversation_id or uuid4(),
        analysis_id="sales-drop-2026",
        agent_type="explorer",
        session_id="orders-v1",
    )


class AnalysisQueryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_query_has_end_to_end_hard_timeout(self) -> None:
        repo = HangingQueryRepo([])
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, RecordingGuard()),
            repo,
            store,
            make_limits(timeout_seconds=1),
        )

        with self.assertRaises(QueryExecutionTimeoutError):
            await service.execute(make_session_key(), "SELECT raw")

        self.assertEqual(store.uploads, [])

    def test_estimates_doris_scan_rows_and_bytes(self) -> None:
        estimate = estimate_doris_query_plan(
            (
                "0:VOlapScanNode",
                "cardinality=120, avgRowSize=16.5, numNodes=3",
                "1:VEXCHANGE",
                "2:VOlapScanNode",
                "cardinality=80, avgRowSize=10",
            ),
            require_scan=True,
        )

        self.assertEqual(estimate.scan_nodes, 2)
        self.assertEqual(estimate.scan_rows, 200)
        self.assertEqual(estimate.scan_bytes, 2780)

    def test_estimates_thousands_separated_doris_plan_values(self) -> None:
        estimate = estimate_doris_query_plan(
            (
                "0:VOlapScanNode",
                "cardinality=1,500,000, avgRowSize=1,024.5",
            ),
            require_scan=True,
        )

        self.assertEqual(estimate.scan_rows, 1_500_000)
        self.assertEqual(estimate.scan_bytes, 1_536_750_000)

    def test_rejects_malformed_plan_numbers_instead_of_using_prefix(self) -> None:
        with self.assertRaises(QueryPlanUnavailableError):
            estimate_doris_query_plan(
                (
                    "0:VOlapScanNode",
                    "cardinality=12,34, avgRowSize=8",
                ),
                require_scan=True,
            )

    def test_unnumbered_plan_nodes_cannot_overwrite_scan_estimates(self) -> None:
        estimate = estimate_doris_query_plan(
            (
                "0:VOlapScanNode",
                "cardinality=100000000, avgRowSize=100",
                "VAGGREGATION_NODE",
                "cardinality=1, avgRowSize=1",
            ),
            require_scan=True,
        )

        self.assertEqual(estimate.scan_rows, 100_000_000)
        self.assertEqual(estimate.scan_bytes, 10_000_000_000)

    def test_each_unnumbered_scan_requires_its_own_estimate(self) -> None:
        with self.assertRaises(QueryPlanUnavailableError):
            estimate_doris_query_plan(
                (
                    "VFileScanNode",
                    "cardinality=100, avgRowSize=10",
                    "VFileScanNode",
                ),
                require_scan=True,
            )

    def test_requires_complete_scan_estimates_for_physical_tables(self) -> None:
        with self.assertRaises(QueryPlanUnavailableError):
            estimate_doris_query_plan(("PLAN FRAGMENT 0",), require_scan=True)
        with self.assertRaises(QueryPlanUnavailableError):
            estimate_doris_query_plan(
                (
                    "0:VOlapScanNode",
                    "cardinality=10000000, avgRowSize=0.0",
                ),
                require_scan=True,
            )

    async def test_scan_limit_rejects_before_stream_and_upload(self) -> None:
        guard = RecordingGuard(physical_table=True)
        repo = FakeQueryRepo(
            [QueryBatch(column_names=("id",), rows=((1,),))],
            plan=(
                "0:VOlapScanNode",
                "cardinality=1001, avgRowSize=8",
            ),
        )
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, guard),
            repo,
            store,
            make_limits(max_scan_rows=1000),
        )

        with self.assertRaises(QueryScanLimitExceededError):
            await service.execute(make_session_key(), "SELECT raw")

        self.assertIsNone(repo.sql)
        self.assertEqual(store.uploads, [])

    async def test_guarded_batches_are_written_and_summarized(self) -> None:
        timestamp_1 = datetime(2026, 1, 2, 3, tzinfo=UTC)
        timestamp_2 = datetime(2026, 1, 3, 4, tzinfo=UTC)
        guard = RecordingGuard()
        repo = FakeQueryRepo(
            [
                QueryBatch(
                    column_names=("id", "amount", "created_at"),
                    rows=((1, Decimal("12.50"), timestamp_2),),
                ),
                QueryBatch(
                    column_names=("id", "amount", "created_at"),
                    rows=((2, None, timestamp_1), (3, Decimal("1.20"), timestamp_2)),
                ),
            ]
        )
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, guard),
            repo,
            store,
            make_limits(),
        )
        conversation_id = uuid4()
        session_key = make_session_key(conversation_id)

        result = await service.execute(session_key, "SELECT raw")

        self.assertEqual(guard.calls, [(9, "SELECT raw", "doris")])
        self.assertEqual(repo.explain_sql, "SELECT normalized")
        self.assertEqual(repo.sql, "SELECT normalized")
        self.assertEqual(result.row_count, 3)
        self.assertEqual(result.schema[1].type, "decimal")
        self.assertTrue(result.schema[1].nullable)
        self.assertEqual(
            result.time_range["created_at"].model_dump(),
            {
                "start": "2026-01-02T03:00:00+00:00",
                "end": "2026-01-03T04:00:00+00:00",
            },
        )
        self.assertEqual(len(result.sample), 2)
        self.assertEqual(len(store.uploads), 1)
        user_id, uploaded_conversation_id, path, content = store.uploads[0]
        self.assertEqual((user_id, uploaded_conversation_id), (9, conversation_id))
        self.assertEqual(result.path, f"/{path}")
        self.assertTrue(
            result.path.startswith(
                "/analyses/sales-drop-2026/sessions/explorer/orders-v1/query_"
            )
        )
        rows = list(csv.reader(io.StringIO(content.decode())))
        self.assertEqual(rows[0], ["id", "amount", "created_at"])
        self.assertEqual(rows[1][1], "12.50")

    async def test_row_limit_discards_temporary_artifact(self) -> None:
        guard = RecordingGuard()
        repo = FakeQueryRepo(
            [
                QueryBatch(
                    column_names=("id",),
                    rows=((1,), (2,), (3,)),
                )
            ]
        )
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, guard),
            repo,
            store,
            make_limits(max_rows=2),
        )

        with self.assertRaises(QueryResultLimitExceededError):
            await service.execute(make_session_key(), "SELECT raw")

        self.assertEqual(store.uploads, [])

    async def test_empty_query_still_writes_header_and_unknown_schema(self) -> None:
        guard = RecordingGuard()
        repo = FakeQueryRepo([QueryBatch(column_names=("id",), rows=())])
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, guard),
            repo,
            store,
            make_limits(),
        )

        result = await service.execute(make_session_key(), "SELECT raw")

        self.assertEqual(result.row_count, 0)
        self.assertEqual(result.schema[0].type, "unknown")
        self.assertTrue(result.schema[0].nullable)
        self.assertEqual(store.uploads[0][3], b"id\n")

    async def test_guard_rejection_never_requests_doris_connection(self) -> None:
        connection_provider = FailingConnectionProvider()
        query_repo = DorisQueryRepository(cast(Any, connection_provider))
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, RejectingGuard()),
            query_repo,
            store,
            make_limits(),
        )

        with self.assertRaises(QueryRejectedError):
            await service.execute(make_session_key(), "DELETE FROM orders")

        self.assertEqual(connection_provider.calls, 0)
        self.assertEqual(store.uploads, [])

    async def test_utf8_output_byte_limit_prevents_artifact_upload(self) -> None:
        guard = RecordingGuard()
        repo = FakeQueryRepo([QueryBatch(column_names=("text",), rows=(("你好",),))])
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, guard),
            repo,
            store,
            make_limits(max_output_bytes=11),
        )

        with self.assertRaises(QueryOutputLimitExceededError):
            await service.execute(make_session_key(), "SELECT raw")

        self.assertTrue(repo.closed)
        self.assertEqual(store.uploads, [])

    async def test_sample_truncation_does_not_truncate_csv_artifact(self) -> None:
        long_value = "x" * 1000
        guard = RecordingGuard()
        repo = FakeQueryRepo(
            [QueryBatch(column_names=("text",), rows=((long_value,),))]
        )
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, guard),
            repo,
            store,
            make_limits(max_output_bytes=2048),
        )

        result = await service.execute(make_session_key(), "SELECT raw")

        self.assertEqual(result.sample[0]["text"], f"{'x' * 512}…")
        self.assertIn(long_value.encode(), store.uploads[0][3])

    async def test_csv_formula_strings_and_headers_are_escaped(self) -> None:
        guard = RecordingGuard()
        repo = FakeQueryRepo(
            [
                QueryBatch(
                    column_names=("=formula_header", "plain"),
                    rows=(
                        ("=1+1", "ordinary"),
                        (" \t@SUM(A1)", "+123"),
                        ("\x01-CMD", 42),
                    ),
                )
            ]
        )
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            cast(QueryGuardService, guard),
            repo,
            store,
            make_limits(max_output_bytes=2048, sample_rows=3),
        )

        result = await service.execute(make_session_key(), "SELECT raw")

        rows = list(csv.reader(io.StringIO(store.uploads[0][3].decode())))
        self.assertEqual(rows[0], ["'=formula_header", "plain"])
        self.assertEqual(rows[1], ["'=1+1", "ordinary"])
        self.assertEqual(rows[2], ["' \t@SUM(A1)", "'+123"])
        self.assertEqual(rows[3], ["'\x01-CMD", "42"])
        self.assertEqual(result.sample[0]["=formula_header"], "=1+1")
