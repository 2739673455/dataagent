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

from app.assistant.agents.tools.execute_sql import create_execute_sql_tool
from app.identity import errors as auth_error
from app.identity.errors import QueryPrincipalNotConfiguredError
from app.identity.services.identity import IdentityService
from app.query.errors import (
    QueryExecutionTimeoutError,
    QueryRejectedError,
    QueryResultShapeError,
)
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryBatch,
    QueryExecutionOptions,
)
from app.query.models.validation import QueryValidationResult
from app.query.repositories.doris import DorisQueryRepository
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.executor import AnalysisQueryService


def valid():
    return QueryValidationResult(valid=True, normalized_sql="SELECT 1 AS value")


def rejected():
    return QueryValidationResult(
        valid=False,
        normalized_sql=None,
        issues=["不允许访问"],
    )


def result():
    return AnalysisQueryResult(path="/result.csv", columns=[], row_count=0, sample=[])


def key():
    return SimpleNamespace(user_id=7, conversation_id=uuid4())


class QueryPrincipalTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_user_or_missing_identity_never_loads_grants(self):
        for user, identity, expected in (
            (None, None, auth_error.UserSelectionRequiredError),
            (
                SimpleNamespace(doris_role_name="reader"),
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
                    await IdentityService(repo).get_query_principal(7, cipher)

                cipher.decrypt.assert_not_called()


class QueryHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = key()
        self.principal = SimpleNamespace(
            role_name="reader", query_user="query_reader", password="password"
        )
        self.service = MagicMock(execute=AsyncMock(return_value=result()))
        self.identity = MagicMock(
            get_query_principal=AsyncMock(return_value=self.principal)
        )
        self.guard = MagicMock(check=MagicMock(return_value=valid()))
        self.clients = MagicMock(get_or_create=MagicMock())
        for name, value in (
            ("IdentityService", self.identity),
            ("QueryGuardService", self.guard),
            ("AnalysisQueryService", self.service),
        ):
            patcher = patch(
                f"app.query.services.execution_handler.{name}", return_value=value
            )
            patcher.start()
            self.addCleanup(patcher.stop)

        @asynccontextmanager
        async def transaction():
            yield

        @asynccontextmanager
        async def session():
            yield MagicMock(begin=transaction)

        self.handler = QueryExecutionHandler(
            MagicMock(), MagicMock(session=session), self.clients
        )
        self.tool = create_execute_sql_tool(self.handler)
        self.tool_runtime = ToolRuntime(
            state={},
            context=None,
            config={
                "configurable": {
                    "user_id": self.key.user_id,
                    "conversation_id": str(self.key.conversation_id),
                }
            },
            stream_writer=lambda _: None,
            tool_call_id="call-1",
            store=None,
        )

    async def execute(self):
        return await self.handler.execute(
            self.key.user_id,
            self.key.conversation_id,
            " select 1 as value ",
            purpose="统计",
        )

    async def test_success_returns_executor_result(self):
        actual = await self.execute()
        self.assertIs(actual, self.service.execute.return_value)
        self.service.execute.assert_awaited_once_with(
            self.key.user_id,
            self.key.conversation_id,
            self.guard.check.return_value.normalized_sql,
            purpose="统计",
        )

    async def test_rejection_never_creates_executor(self):
        self.guard.check.return_value = rejected()
        with self.assertRaises(QueryRejectedError):
            await self.execute()
        self.clients.get_or_create.assert_not_called()

    async def test_tool_returns_error_details_without_codes_and_handler_preserves_error(
        self,
    ):
        for error in (
            QueryRejectedError(rejected()),
            QueryExecutionTimeoutError("超时"),
            QueryResultShapeError("列结构错误"),
            OSError("artifact unavailable"),
        ):
            with self.subTest(error=type(error).__name__):
                self.service.execute.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    await self.execute()
                self.assertIs(raised.exception, error)
                payload = await self.tool.ainvoke(
                    {"runtime": self.tool_runtime, "sql": "SELECT 1", "purpose": "统计"}
                )
                self.assertNotIn("code", payload)
                self.assertEqual(payload["status"], "error")
                if isinstance(error, QueryRejectedError):
                    self.assertEqual(
                        payload["validation"], error.result.model_dump(mode="json")
                    )
                else:
                    self.assertEqual(payload["details"][0]["msg"], str(error))

    async def test_cancellation_propagates_through_tool(self):
        self.service.execute.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.tool.ainvoke(
                {"runtime": self.tool_runtime, "sql": "SELECT 1", "purpose": "统计"}
            )

    async def test_identity_failure_does_not_execute(self):
        self.identity.get_query_principal.side_effect = (
            QueryPrincipalNotConfiguredError("no role")
        )
        with self.assertRaises(QueryPrincipalNotConfiguredError):
            await self.execute()
        self.guard.check.assert_not_called()
        self.clients.get_or_create.assert_not_called()


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
            QueryExecutionOptions(batch_size=2, sample_rows=1),
        )

    async def execute(self):
        return await self.service.execute(
            self.key.user_id,
            self.key.conversation_id,
            "SELECT 1 AS value",
            purpose="统计",
        )

    async def test_multibatch_output_scope_and_bounded_sample(self):
        actual = await self.execute()
        self.assertEqual(actual.row_count, 3)
        self.assertEqual(actual.sample, [{"value": 1}])
        user, conversation, path, content = self.files[0]
        self.assertEqual(
            (user, conversation), (self.key.user_id, self.key.conversation_id)
        )
        self.assertRegex(path, r"^统计_[a-f0-9]{32}\.csv$")
        self.assertEqual(actual.path, f"/data/{self.key.conversation_id}/{path}")
        second = await self.execute()
        self.assertNotEqual(actual.path, second.path)
        self.assertEqual(
            list(csv.reader(io.StringIO(content.decode()))),
            [["value"], ["1"], ["2"], ["3"]],
        )

    async def test_empty_result_preserves_header_and_schema(self):
        self.batches = [QueryBatch(("value",), ())]
        actual = await self.execute()
        self.assertEqual(actual.row_count, 0)
        self.assertEqual(actual.columns, ["value"])
        self.assertEqual(self.files[0][3], b"value\n")

    async def test_invalid_shapes_close_stream_without_upload(self):
        for batches in (
            [],
            [QueryBatch(("v", "V"), ())],
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

        self.repo = DorisQueryRepository(
            MagicMock(engine=MagicMock(connect=connection))
        )
        self.options = QueryExecutionOptions(batch_size=2)

    async def consume(self):
        return [
            batch async for batch in self.repo.stream("SELECT ':literal'", self.options)
        ]

    async def test_query_uses_database_defaults_and_closes_cursor(self):
        batches = await self.consume()
        self.assertEqual(batches[0].rows, ((1,),))
        self.connection.execute.assert_not_awaited()
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


class QuerySessionBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_role_credentials_are_used_and_doris_runs_outside_pg_sessions(self):
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
            query_user="query_reader",
            encrypted_password="encrypted",
            workload_group="readers",
        )
        repo = MagicMock(
            get_user_by_id=AsyncMock(
                return_value=SimpleNamespace(id=7, doris_role_name="reader")
            ),
            get_query_identity=AsyncMock(return_value=identity),
        )
        clients = MagicMock(get_or_create=MagicMock(return_value=MagicMock()))
        store = MagicMock(write_artifact=AsyncMock())
        guard = MagicMock(check=MagicMock(return_value=valid()))

        async def stream(*args):
            self.assertEqual(active, set())
            yield QueryBatch(("value",), ((1,),))

        def validate(*args):
            self.assertEqual(active, set())
            return valid()

        guard.check.side_effect = validate
        with (
            patch(
                "app.query.services.execution_handler.DorisCredentialCipher",
                return_value=MagicMock(decrypt=lambda _: "password"),
            ),
            patch(
                "app.query.services.execution_handler.IdentityPGRepo", return_value=repo
            ),
            patch(
                "app.query.services.execution_handler.QueryGuardService",
                return_value=guard,
            ),
            patch.object(DorisQueryRepository, "stream", stream),
        ):
            handler = QueryExecutionHandler(
                store,
                manager("auth"),
                clients,
            )
            actual = await handler.execute(7, uuid4(), "SELECT 1", purpose="统计")
        self.assertEqual(actual.row_count, 1)
        repo.get_user_by_id.assert_awaited_once_with(7)
        repo.get_query_identity.assert_awaited_once_with("reader")
        clients.get_or_create.assert_called_once_with(
            "reader", "query_reader", "password"
        )
        guard.check.assert_called_once_with("SELECT 1")
        self.assertEqual(
            events,
            [
                ("auth", "enter"),
                ("auth", "exit"),
            ],
        )
