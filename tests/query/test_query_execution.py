"""查询用例、流式产物与数据库会话边界回归。"""

import asyncio
import csv
import io
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from langchain.tools import ToolRuntime

from app.assistant.agents.explorer.tools.execute_sql import _execute_sql
from app.identity import errors as auth_error
from app.identity.models.doris import DorisAuthorizationSnapshot
from app.identity.services.query_principal import (
    QueryPrincipalNotConfiguredError,
    QueryPrincipalService,
)
from app.query.errors import QueryRejectedError, QueryResultShapeError
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryBatch,
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryExecutionTimeoutError,
)
from app.query.models.validation import QueryValidationIssue, QueryValidationResult
from app.query.repositories.doris import DorisQueryRepository
from app.query.runtime import DatabaseQueryExecutionRuntime
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.execution_recorder import (
    QueryExecutionContext,
    QueryExecutionRecorder,
)
from app.query.services.executor import AnalysisQueryService
from app.shared.contracts.analysis import AgentSessionKey


def valid():
    return QueryValidationResult(valid=True, normalized_sql="SELECT 1 AS value")


def rejected():
    return QueryValidationResult(
        valid=False,
        normalized_sql=None,
        issues=[QueryValidationIssue(code="denied", message="不允许访问")],
    )


def result():
    return AnalysisQueryResult(
        path="/result.csv", columns=[], row_count=0, time_range={}, sample=[]
    )


def key():
    return AgentSessionKey(7, uuid4(), "sales", "explorer", "daily")


class QueryPrincipalTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_user_or_missing_identity_never_loads_grants(self):
        for user, identity, expected in (
            (None, None, auth_error.UserNotFoundError),
            (SimpleNamespace(is_active=False), None, auth_error.InactiveUserError),
            (
                SimpleNamespace(is_active=True, doris_role_name=None),
                None,
                QueryPrincipalNotConfiguredError,
            ),
            (
                SimpleNamespace(is_active=True, doris_role_name="reader"),
                None,
                QueryPrincipalNotConfiguredError,
            ),
        ):
            with self.subTest(expected=expected):
                repo = MagicMock(
                    get_user_by_id=AsyncMock(return_value=user),
                    get_query_identity=AsyncMock(return_value=identity),
                )
                cipher = MagicMock()
                with self.assertRaises(expected):
                    await QueryPrincipalService(
                        repo,
                        cipher,
                        MagicMock(
                            observe_role=AsyncMock(
                                side_effect=auth_error.RoleNotFoundError
                            )
                        ),
                    ).resolve(7)

                cipher.decrypt.assert_not_called()


class QueryHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = key()
        self.principal = SimpleNamespace(
            role_name="reader", authorization_fingerprint="a" * 64
        )
        self.service = MagicMock(execute=AsyncMock(return_value=result()))
        self.runtime = MagicMock(
            resolve_principal=AsyncMock(return_value=(self.principal, object())),
            validate=AsyncMock(return_value=valid()),
            create_executor=AsyncMock(return_value=self.service),
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        self.handler = QueryExecutionHandler(self.runtime)
        self.tool_runtime = ToolRuntime(
            state={},
            context=None,
            config={
                "configurable": {
                    "user_id": self.key.user_id,
                    "conversation_id": str(self.key.conversation_id),
                    "analysis_id": self.key.analysis_id,
                    "session_id": self.key.session_id,
                }
            },
            stream_writer=lambda _: None,
            tool_call_id="call-1",
            store=None,
        )

    async def execute(self):
        return await self.handler.execute(
            self.key, " select 1 as value ", purpose="统计", tool_call_id="call-1"
        )

    async def test_success_record_failure_does_not_change_result(self):
        self.runtime.record_success.side_effect = OSError("record unavailable")
        actual = await self.execute()
        self.assertIs(actual, self.service.execute.return_value)
        args = self.runtime.record_success.call_args
        self.assertEqual(args.args[0].session_key, self.key)
        self.assertEqual(args.args[0].role_name, "reader")
        self.assertEqual(args.kwargs["raw_sql"], " select 1 as value ")
        self.assertIs(args.kwargs["validation"], self.runtime.validate.return_value)
        self.assertIs(args.kwargs["result"], actual)
        self.runtime.record_failure.assert_not_awaited()

    async def test_rejection_never_creates_executor(self):
        self.runtime.validate.return_value = rejected()
        with self.assertRaises(QueryRejectedError):
            await self.execute()
        self.runtime.create_executor.assert_not_awaited()
        self.assertEqual(
            self.runtime.record_failure.call_args.kwargs["status"], "rejected"
        )

    async def test_tool_and_record_share_codes_and_record_failure_preserves_error(self):
        for error, code in (
            (QueryRejectedError(rejected()), "sql_validation_failed"),
            (QueryExecutionTimeoutError("超时"), "query_timeout"),
            (QueryResultShapeError("列结构错误"), "query_result_invalid"),
            (OSError("artifact unavailable"), "readonly_query_failed"),
        ):
            with self.subTest(code=code):
                self.service.execute.side_effect = error
                self.runtime.record_failure.side_effect = OSError("record unavailable")
                with self.assertRaises(type(error)) as raised:
                    await self.execute()
                self.assertIs(raised.exception, error)
                payload = await _execute_sql(
                    self.handler, self.tool_runtime, "SELECT 1", "统计"
                )
                self.assertEqual(payload["code"], code)
                self.assertEqual(
                    self.runtime.record_failure.call_args.kwargs["error_code"], code
                )
                if isinstance(error, QueryRejectedError):
                    self.assertEqual(
                        payload["validation"], error.result.model_dump(mode="json")
                    )
                else:
                    self.assertEqual(payload["details"][0]["msg"], str(error))

    async def test_cancellation_propagates_through_tool_without_failure_record(self):
        self.service.execute.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await _execute_sql(self.handler, self.tool_runtime, "SELECT 1")
        self.runtime.record_failure.assert_not_awaited()
        self.runtime.record_success.assert_not_awaited()

    async def test_identity_failure_does_not_execute_or_record_under_unknown_role(self):
        self.runtime.resolve_principal.side_effect = QueryPrincipalNotConfiguredError(
            "no role"
        )
        with self.assertRaises(QueryPrincipalNotConfiguredError):
            await self.execute()
        self.runtime.validate.assert_not_awaited()
        self.runtime.create_executor.assert_not_awaited()
        self.runtime.record_failure.assert_not_awaited()


class QueryExecutorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = key()
        self.closed = False
        self.files = []
        self.batches = [
            QueryBatch(("value",), ((1,), (2,))),
            QueryBatch(("value",), ((3,),)),
        ]

        async def stream(*args):
            try:
                for batch in self.batches:
                    yield batch
            finally:
                self.closed = True

        async def write(user_id, conversation_id, path, content):
            self.assertTrue(self.closed)
            self.files.append((user_id, conversation_id, path, content.read()))

        self.store = MagicMock(write_artifact=AsyncMock(side_effect=write))
        self.service = AnalysisQueryService(
            MagicMock(stream=stream),
            self.store,
            QueryExecutionLimits(
                workload_group="readers", timeout_seconds=30, memory_limit_bytes=1024
            ),
            QueryExecutionOptions(batch_size=2, sample_rows=1),
        )

    async def execute(self):
        return await self.service.execute(self.key, valid(), purpose="统计")

    async def test_multibatch_output_scope_and_bounded_sample(self):
        actual = await self.execute()
        self.assertEqual(actual.row_count, 3)
        self.assertEqual(actual.sample, [{"value": 1}])
        user, conversation, path, content = self.files[0]
        self.assertEqual(
            (user, conversation), (self.key.user_id, self.key.conversation_id)
        )
        self.assertIn("sales", path)
        self.assertIn("explorer", path)
        self.assertIn("daily", path)
        self.assertEqual(path.rsplit("/", 1)[-1], "统计.csv")
        self.assertTrue(actual.path.endswith("/统计.csv"))
        self.assertEqual(
            list(csv.reader(io.StringIO(content.decode()))),
            [["value"], ["1"], ["2"], ["3"]],
        )

    async def test_empty_result_preserves_header_and_schema(self):
        self.batches = [QueryBatch(("value",), ())]
        actual = await self.execute()
        self.assertEqual(actual.row_count, 0)
        self.assertTrue(actual.columns[0].nullable)
        self.assertEqual(self.files[0][3], b"value\n")

    async def test_invalid_shapes_close_stream_without_upload(self):
        for batches in (
            [],
            [QueryBatch(("v", "V"), ())],
            [QueryBatch(("v",), ((1, 2),))],
            [QueryBatch(("v",), ((1,),)), QueryBatch(("other",), ((2,),))],
        ):
            with self.subTest(batches=batches):
                self.batches = batches
                with self.assertRaises(QueryResultShapeError):
                    await self.execute()
                self.assertTrue(self.closed)
                self.store.write_artifact.assert_not_awaited()

    async def test_upload_failure_propagates_and_closes_temporary_file(self):
        error = OSError("upload failed")
        self.store.write_artifact.side_effect = error
        with self.assertRaises(OSError) as raised:
            await self.execute()
        self.assertIs(raised.exception, error)
        self.assertTrue(self.closed)
        self.assertTrue(self.store.write_artifact.call_args.args[3].closed)

    async def test_invalid_validation_does_not_execute(self):
        with self.assertRaises(QueryRejectedError):
            await self.service.execute(self.key, rejected(), purpose="统计")
        self.assertFalse(self.closed)
        self.store.write_artifact.assert_not_awaited()


class DorisStreamTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rows_error = None
        self.exited = False

        async def partitions(size):
            self.assertEqual(size, 2)
            if self.rows_error is not None:
                raise self.rows_error
            yield [(1,)]

        self.result = MagicMock(
            keys=lambda: ["v"], partitions=partitions, close=AsyncMock()
        )
        self.connection = MagicMock(
            execute=AsyncMock(),
            stream=AsyncMock(return_value=self.result),
            invalidate=AsyncMock(),
        )

        @asynccontextmanager
        async def connection():
            try:
                yield self.connection
            finally:
                self.exited = True

        self.repo = DorisQueryRepository(MagicMock(connection=connection))
        self.limits = QueryExecutionLimits(
            workload_group="readers", timeout_seconds=17, memory_limit_bytes=2048
        )
        self.options = QueryExecutionOptions(batch_size=2)

    async def consume(self):
        return [
            batch
            async for batch in self.repo.stream(
                "SELECT ':literal'", self.limits, self.options
            )
        ]

    async def test_limits_and_cursor_cleanup(self):
        batches = await self.consume()
        self.assertEqual(batches[0].rows, ((1,),))
        self.assertEqual(
            [str(c.args[0]) for c in self.connection.execute.call_args_list],
            [
                "SET workload_group = 'readers'",
                "SET query_timeout = 17",
                "SET exec_mem_limit = 2048",
            ],
        )
        self.assertEqual(self.connection.stream.call_args.args[0].compile().params, {})
        self.result.close.assert_awaited_once()
        self.assertTrue(self.exited)

    async def test_timeout_wraps_original_error_and_closes_cursor(self):
        self.rows_error = TimeoutError("timed out")
        with self.assertRaises(QueryExecutionTimeoutError) as raised:
            await self.consume()
        self.assertIs(raised.exception.__cause__, self.rows_error)
        self.result.close.assert_awaited_once()
        self.assertTrue(self.exited)

    async def test_cancel_invalidates_connection_and_closes_cursor(self):
        self.rows_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.consume()
        self.connection.invalidate.assert_awaited_once()
        self.result.close.assert_awaited_once()
        self.assertTrue(self.exited)


class QueryRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_identity_policy_share_reads_and_doris_runs_outside_pg_sessions(self):
        active = set()
        events = []

        @asynccontextmanager
        async def transaction():
            yield

        def manager(name):
            @asynccontextmanager
            async def session():
                active.add(name)
                events.append((name, "enter"))
                try:
                    yield MagicMock(begin=lambda: transaction())
                finally:
                    active.remove(name)
                    events.append((name, "exit"))

            return MagicMock(session=session)

        identity = SimpleNamespace(
            role_name="reader",
            authorization_fingerprint="same",
            query_user="query_reader",
            encrypted_password="encrypted",
            workload_group="readers",
        )
        repo = MagicMock(
            get_user_by_id=AsyncMock(
                return_value=SimpleNamespace(
                    id=7, is_active=True, doris_role_name="reader"
                )
            ),
            get_query_identity=AsyncMock(return_value=identity),
            lock_query_identity=AsyncMock(return_value=identity),
        )
        clients = MagicMock(get_or_create=AsyncMock(return_value=MagicMock()))
        recorder = MagicMock(record_success=AsyncMock())
        store = MagicMock(write_artifact=AsyncMock())
        guard = MagicMock(check=AsyncMock(return_value=valid()))

        async def stream(*args):
            self.assertEqual(active, set())
            yield QueryBatch(("value",), ((1,),))

        async def validate(*args):
            self.assertEqual(active, {"meta"})
            return valid()

        async def record(*args, **kwargs):
            self.assertEqual(active, {"meta"})

        guard.check.side_effect = validate
        recorder.record_success.side_effect = record
        with (
            patch(
                "app.query.runtime.DorisCredentialCipher",
                return_value=MagicMock(decrypt=lambda _: "password"),
            ),
            patch("app.query.runtime.IdentityPGRepo", return_value=repo),
            patch(
                "app.query.runtime.DorisRoleRepository",
                return_value=MagicMock(
                    read_authorization=AsyncMock(
                        return_value=DorisAuthorizationSnapshot((), "same")
                    )
                ),
            ),
            patch("app.query.runtime.QueryGuardService", return_value=guard),
            patch.object(DorisQueryRepository, "stream", stream),
        ):
            runtime = DatabaseQueryExecutionRuntime(
                store,
                lambda _: recorder,
                manager("auth"),
                manager("meta"),
                clients,
                MagicMock(),
            )
            actual = await QueryExecutionHandler(runtime).execute(
                key(), "SELECT 1", purpose="统计", tool_call_id=None
            )
        self.assertEqual(actual.row_count, 1)
        repo.get_user_by_id.assert_awaited_once_with(7)
        repo.lock_query_identity.assert_awaited_once_with("reader")
        clients.get_or_create.assert_awaited_once_with(
            "reader", "query_reader", "password"
        )
        policy = guard.check.call_args.args[1]
        context = recorder.record_success.call_args.args[0]
        self.assertEqual(
            (policy.role_name, policy.authorization_fingerprint),
            (context.role_name, context.authorization_fingerprint),
        )
        self.assertEqual(
            events,
            [
                ("auth", "enter"),
                ("auth", "exit"),
                ("meta", "enter"),
                ("meta", "exit"),
                ("meta", "enter"),
                ("meta", "exit"),
            ],
        )


class QueryRecorderTest(unittest.IsolatedAsyncioTestCase):
    async def test_business_aggregates_experience_catalog_only_records_and_enqueue_after_commit(
        self,
    ):
        for kind in ("business", "catalog"):
            with self.subTest(kind=kind):
                self.transaction_active = False

                @asynccontextmanager
                async def transaction():
                    self.transaction_active = True
                    try:
                        yield
                    finally:
                        self.transaction_active = False

                stored = SimpleNamespace(id=uuid4(), revision=3)
                executions = MagicMock(record=AsyncMock())
                experiences = MagicMock(
                    session=MagicMock(begin=transaction),
                    metadata_versions=AsyncMock(return_value=({}, {})),
                    upsert_from_success=AsyncMock(return_value=stored),
                )
                scheduler = MagicMock()
                scheduler.enqueue.side_effect = lambda *_: self.assertFalse(
                    self.transaction_active
                )
                recorder = QueryExecutionRecorder(
                    executions,
                    experiences,
                    scheduler,
                    data_source="doris",
                    database_name="analytics",
                )
                context = QueryExecutionContext(key(), "reader", "a" * 64, "统计")
                validation = QueryValidationResult(
                    valid=True, normalized_sql="SELECT 1 AS value", query_kind=kind
                )
                recorded = await recorder.record_success(
                    context,
                    raw_sql="select 1 as value",
                    validation=validation,
                    result=result(),
                )
                execution = executions.record.call_args.args[0]
                self.assertEqual(execution.user_id, context.session_key.user_id)
                self.assertEqual(execution.raw_sql, "select 1 as value")
                self.assertEqual(execution.normalized_sql, validation.normalized_sql)
                self.assertNotIn("sample", execution.result_summary)
                if kind == "business":
                    self.assertEqual(recorded, stored.id)
                    experience = experiences.upsert_from_success.call_args.args[0]
                    self.assertEqual(
                        experience.authorization_fingerprint,
                        context.authorization_fingerprint,
                    )
                    self.assertEqual(experience.purposes, ["统计"])
                    self.assertIn(":p1", experience.sql_template)
                    scheduler.enqueue.assert_called_once_with(stored.id, 3)
                else:
                    self.assertIsNone(recorded)
                    experiences.upsert_from_success.assert_not_awaited()
                    scheduler.enqueue.assert_not_called()
