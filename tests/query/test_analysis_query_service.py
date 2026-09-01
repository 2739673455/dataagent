import csv
import io
import unittest
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from typing import BinaryIO
from uuid import UUID, uuid4

from app.query.models.execution import (
    QueryBatch,
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryExecutionTimeoutError,
)
from app.query.models.validation import (
    QueryKind,
    QueryTableRef,
    QueryValidationIssue,
    QueryValidationResult,
)
from app.query.services.executor import (
    AnalysisQueryService,
    QueryPlanUnavailableError,
    QueryRejectedError,
    _estimate_doris_query_plan,
    _query_artifact_filename,
)
from app.shared.contracts.analysis import AgentSessionKey


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
        options: QueryExecutionOptions,
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


class TimingOutQueryRepo(FakeQueryRepo):
    async def explain(
        self,
        sql: str,
        limits: QueryExecutionLimits,
    ) -> tuple[str, ...]:
        self.explain_sql = sql
        raise QueryExecutionTimeoutError(
            f"Doris 查询执行超时，最大允许 {limits.timeout_seconds} 秒"
        )


def make_limits(**updates: int) -> QueryExecutionLimits:
    values = {
        "workload_group": "dataagent_readonly",
        "timeout_seconds": 10,
        "memory_limit_bytes": 1024,
    }
    values.update(updates)
    return QueryExecutionLimits.model_validate(values)


def make_options(**updates: int) -> QueryExecutionOptions:
    values = {"batch_size": 2, "sample_rows": 2}
    values.update(updates)
    return QueryExecutionOptions.model_validate(values)


def make_session_key(conversation_id: UUID | None = None) -> AgentSessionKey:
    return AgentSessionKey(
        user_id=9,
        conversation_id=conversation_id or uuid4(),
        analysis_id="sales-drop-2026",
        agent_type="explorer",
        session_id="orders-v1",
    )


def make_validation(
    *,
    physical_table: bool = False,
    query_kind: QueryKind = "business",
) -> QueryValidationResult:
    """构造已经通过 Guard 的查询结果。"""
    return QueryValidationResult(
        valid=True,
        normalized_sql="SELECT normalized",
        query_kind=query_kind,
        tables=(
            [QueryTableRef(database="analytics", name="orders")]
            if physical_table
            else []
        ),
    )


class AnalysisQueryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_query_skips_explain_and_keeps_audit_details(self) -> None:
        repo = FakeQueryRepo(
            [QueryBatch(column_names=("table_name",), rows=(("orders",),))]
        )
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            repo,
            store,
            make_limits(),
            make_options(),
        )

        details = await service.execute(
            make_session_key(),
            "SHOW TABLES",
            make_validation(query_kind="catalog"),
            purpose="列出可用业务表",
        )

        self.assertIsNone(repo.explain_sql)
        self.assertEqual(repo.sql, "SELECT normalized")
        self.assertIsNone(details.plan_estimate)
        self.assertEqual(details.validation.query_kind, "catalog")
        self.assertEqual(details.result.sample, [{"table_name": "orders"}])

    async def test_query_propagates_repository_timeout(self) -> None:
        repo = TimingOutQueryRepo([])
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            repo,
            store,
            make_limits(timeout_seconds=1),
            make_options(),
        )

        with self.assertRaises(QueryExecutionTimeoutError):
            await service.execute(
                make_session_key(),
                "SELECT raw",
                make_validation(),
                purpose="测试超时查询",
            )

        self.assertEqual(store.uploads, [])

    def test_estimates_doris_scan_rows_and_bytes(self) -> None:
        estimate = _estimate_doris_query_plan(
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
        estimate = _estimate_doris_query_plan(
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
            _estimate_doris_query_plan(
                (
                    "0:VOlapScanNode",
                    "cardinality=12,34, avgRowSize=8",
                ),
                require_scan=True,
            )

    def test_unnumbered_plan_nodes_cannot_overwrite_scan_estimates(self) -> None:
        estimate = _estimate_doris_query_plan(
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
            _estimate_doris_query_plan(
                (
                    "VFileScanNode",
                    "cardinality=100, avgRowSize=10",
                    "VFileScanNode",
                ),
                require_scan=True,
            )

    def test_requires_complete_scan_estimates_for_physical_tables(self) -> None:
        with self.assertRaises(QueryPlanUnavailableError):
            _estimate_doris_query_plan(("PLAN FRAGMENT 0",), require_scan=True)
        with self.assertRaises(QueryPlanUnavailableError):
            _estimate_doris_query_plan(
                (
                    "0:VOlapScanNode",
                    "cardinality=10000000, avgRowSize=0.0",
                ),
                require_scan=True,
            )

    async def test_guarded_batches_are_written_and_summarized(self) -> None:
        timestamp_1 = datetime(2026, 1, 2, 3, tzinfo=UTC)
        timestamp_2 = datetime(2026, 1, 3, 4, tzinfo=UTC)
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
            repo,
            store,
            make_limits(),
            make_options(),
        )
        conversation_id = uuid4()
        session_key = make_session_key(conversation_id)

        details = await service.execute(
            session_key,
            "SELECT raw",
            make_validation(),
            purpose="统计订单金额及创建时间",
        )
        result = details.result

        self.assertEqual(repo.explain_sql, "SELECT normalized")
        self.assertEqual(repo.sql, "SELECT normalized")
        self.assertEqual(result.row_count, 3)
        self.assertEqual(result.columns[1].type, "decimal")
        self.assertTrue(result.columns[1].nullable)
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
        self.assertEqual(result.path, f"/data/{conversation_id}/{path}")
        self.assertTrue(
            result.path.startswith(
                f"/data/{conversation_id}/sessions/sales-drop-2026/explorer/"
                "orders-v1/统计订单金额及创建时间_"
            )
        )
        self.assertRegex(result.path, r"_[0-9a-f]{4}\.csv$")
        rows = list(csv.reader(io.StringIO(content.decode())))
        self.assertEqual(rows[0], ["id", "amount", "created_at"])
        self.assertEqual(rows[1][1], "12.50")

    async def test_returns_lineage_after_artifact_commit(self) -> None:
        repo = FakeQueryRepo(
            [QueryBatch(column_names=("id",), rows=((1,),))],
            plan=("0:VOlapScanNode", "cardinality=1, avgRowSize=8"),
        )
        store = RecordingArtifactStore()
        session_key = make_session_key()
        service = AnalysisQueryService(
            repo,
            store,
            make_limits(),
            make_options(),
        )

        details = await service.execute(
            session_key,
            "SELECT raw",
            make_validation(physical_table=True),
            purpose="查询订单明细",
        )

        self.assertEqual(len(store.uploads), 1)
        self.assertEqual(details.session_key, session_key)
        self.assertEqual(details.raw_sql, "SELECT raw")
        self.assertEqual(details.normalized_sql, "SELECT normalized")
        self.assertEqual(details.validation.tables[0].name, "orders")
        self.assertEqual(details.result.row_count, 1)

    async def test_all_streamed_rows_are_written(self) -> None:
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
            repo,
            store,
            make_limits(),
            make_options(),
        )

        result = (
            await service.execute(
                make_session_key(),
                "SELECT raw",
                make_validation(),
                purpose="读取全部订单",
            )
        ).result

        self.assertEqual(result.row_count, 3)
        self.assertEqual(store.uploads[0][3], b"id\n1\n2\n3\n")

    async def test_empty_query_still_writes_header_and_unknown_schema(self) -> None:
        repo = FakeQueryRepo([QueryBatch(column_names=("id",), rows=())])
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            repo,
            store,
            make_limits(),
            make_options(),
        )

        result = (
            await service.execute(
                make_session_key(),
                "SELECT raw",
                make_validation(),
                purpose="查询空结果",
            )
        ).result

        self.assertEqual(result.row_count, 0)
        self.assertEqual(result.columns[0].type, "unknown")
        self.assertTrue(result.columns[0].nullable)
        self.assertEqual(store.uploads[0][3], b"id\n")

    async def test_invalid_validation_never_requests_doris_repository(self) -> None:
        query_repo = FakeQueryRepo([])
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            query_repo,
            store,
            make_limits(),
            make_options(),
        )

        with self.assertRaises(QueryRejectedError):
            await service.execute(
                make_session_key(),
                "DELETE FROM orders",
                QueryValidationResult(
                    valid=False,
                    normalized_sql=None,
                    issues=[
                        QueryValidationIssue(
                            code="readonly_query_required",
                            message="只允许只读查询",
                        )
                    ],
                ),
                purpose="执行非法查询",
            )

        self.assertIsNone(query_repo.explain_sql)
        self.assertEqual(store.uploads, [])

    async def test_utf8_output_is_written_without_query_size_limit(self) -> None:
        repo = FakeQueryRepo([QueryBatch(column_names=("text",), rows=(("你好",),))])
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            repo,
            store,
            make_limits(),
            make_options(),
        )

        result = (
            await service.execute(
                make_session_key(),
                "SELECT raw",
                make_validation(),
                purpose="查询中文文本",
            )
        ).result

        self.assertEqual(result.row_count, 1)
        self.assertTrue(repo.closed)
        self.assertEqual(store.uploads[0][3], "text\n你好\n".encode())

    async def test_sample_truncation_does_not_truncate_csv_artifact(self) -> None:
        long_value = "x" * 1000
        repo = FakeQueryRepo(
            [QueryBatch(column_names=("text",), rows=((long_value,),))]
        )
        store = RecordingArtifactStore()
        service = AnalysisQueryService(
            repo,
            store,
            make_limits(),
            make_options(),
        )

        result = (
            await service.execute(
                make_session_key(),
                "SELECT raw",
                make_validation(),
                purpose="查询长文本",
            )
        ).result

        self.assertEqual(result.sample[0]["text"], f"{'x' * 512}…")
        self.assertIn(long_value.encode(), store.uploads[0][3])

    async def test_csv_formula_strings_and_headers_are_escaped(self) -> None:
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
            repo,
            store,
            make_limits(),
            make_options(sample_rows=3),
        )

        result = (
            await service.execute(
                make_session_key(),
                "SELECT raw",
                make_validation(),
                purpose="查询公式文本",
            )
        ).result

        rows = list(csv.reader(io.StringIO(store.uploads[0][3].decode())))
        self.assertEqual(rows[0], ["'=formula_header", "plain"])
        self.assertEqual(rows[1], ["'=1+1", "ordinary"])
        self.assertEqual(rows[2], ["' \t@SUM(A1)", "'+123"])
        self.assertEqual(rows[3], ["'\x01-CMD", "42"])
        self.assertEqual(result.sample[0]["=formula_header"], "=1+1")

    def test_query_artifact_filename_is_readable_safe_and_bounded(self) -> None:
        filename = _query_artifact_filename(
            " 查看订单状态枚举值，确认有效订单口径（排除取消）/最近30天 "
        )

        self.assertRegex(
            filename,
            r"^查看订单状态枚举值_确认有效订单口径_排除取消_最近30天_"
            r"[0-9a-f]{4}\.csv$",
        )
        self.assertLessEqual(len(filename.encode("utf-8")), 129)
