import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.identity.services.authorization import AssetAccessPolicy
from app.query.models.execution import AnalysisQueryResult
from app.query.models.validation import QueryValidationIssue, QueryValidationResult
from app.query.services.execution_handler import (
    QueryExecutionHandler,
    QueryExecutionRuntime,
)
from app.query.services.executor import (
    AnalysisQueryService,
    QueryPlanEstimate,
    QueryRejectedError,
    SuccessfulQueryExecution,
)
from app.query.services.principal import ResolvedQueryPrincipal
from app.shared.contracts.analysis import AgentSessionKey


def make_session_key() -> AgentSessionKey:
    """构造查询会话业务键。"""
    return AgentSessionKey(
        user_id=7,
        conversation_id=uuid4(),
        analysis_id="analysis",
        agent_type="explorer",
        session_id="query",
    )


def make_success(session_key: AgentSessionKey) -> SuccessfulQueryExecution:
    """构造一次成功查询的完整信息。"""
    result = AnalysisQueryResult(
        path="/sessions/a/explorer/s/query.csv",
        columns=[],
        row_count=0,
        time_range={},
        sample=[],
    )
    return SuccessfulQueryExecution(
        session_key=session_key,
        raw_sql="SELECT 1",
        normalized_sql="SELECT 1",
        validation=QueryValidationResult(valid=True, normalized_sql="SELECT 1"),
        plan_estimate=QueryPlanEstimate(scan_nodes=0, scan_rows=0, scan_bytes=0),
        result=result,
    )


class QueryExecutionHandlerTest(unittest.IsolatedAsyncioTestCase):
    def make_handler(
        self,
        execution_service: AnalysisQueryService,
    ) -> tuple[QueryExecutionHandler, MagicMock]:
        """使用查询运行环境替身构造查询处理器。"""
        principal = ResolvedQueryPrincipal(
            role_name="dataagent_standard",
            authorization_epoch=uuid4(),
            query_user="standard_readonly",
            password="query_password",
            workload_group="dataagent_standard",
        )
        runtime = MagicMock(spec=QueryExecutionRuntime)
        runtime.resolve_principal = AsyncMock(
            return_value=(principal, AssetAccessPolicy(user_id=7))
        )
        runtime.validate = AsyncMock(
            return_value=QueryValidationResult(
                valid=True,
                normalized_sql="SELECT 1",
            )
        )
        runtime.create_executor = AsyncMock(return_value=execution_service)
        runtime.record_success = AsyncMock()
        runtime.record_failure = AsyncMock()
        return QueryExecutionHandler(runtime), runtime

    async def test_executes_query_and_records_success(self) -> None:
        session_key = make_session_key()
        details = make_success(session_key)
        execution_service = MagicMock(spec=AnalysisQueryService)
        execution_service.execute = AsyncMock(return_value=details)
        handler, runtime = self.make_handler(execution_service)

        result = await handler.execute(
            session_key,
            "SELECT 1",
            purpose="统计订单",
            tool_call_id="call-1",
        )

        self.assertEqual(result, details.result)
        runtime.create_executor.assert_awaited_once()
        validation = runtime.validate.return_value
        execution_service.execute.assert_awaited_once_with(
            session_key,
            "SELECT 1",
            validation,
        )
        context, recorded_details = runtime.record_success.await_args.args
        self.assertEqual(context.role_name, "dataagent_standard")
        self.assertIsNotNone(context.authorization_epoch)
        self.assertEqual(context.purpose, "统计订单")
        self.assertEqual(context.tool_call_id, "call-1")
        self.assertEqual(recorded_details, details)

    async def test_success_history_failure_keeps_query_result(self) -> None:
        session_key = make_session_key()
        details = make_success(session_key)
        execution_service = MagicMock(spec=AnalysisQueryService)
        execution_service.execute = AsyncMock(return_value=details)
        handler, runtime = self.make_handler(execution_service)
        runtime.record_success = AsyncMock(
            side_effect=RuntimeError("history unavailable")
        )

        result = await handler.execute(
            session_key,
            "SELECT 1",
            purpose="统计订单",
            tool_call_id=None,
        )

        self.assertEqual(result, details.result)

    async def test_rejection_is_recorded_and_reraised(self) -> None:
        session_key = make_session_key()
        validation = QueryValidationResult(
            valid=False,
            normalized_sql=None,
            issues=[
                QueryValidationIssue(
                    code="unknown_column",
                    message="Unknown column",
                )
            ],
        )
        execution_service = MagicMock(spec=AnalysisQueryService)
        execution_service.execute = AsyncMock()
        handler, runtime = self.make_handler(execution_service)
        runtime.validate = AsyncMock(return_value=validation)

        with self.assertRaises(QueryRejectedError) as raised:
            await handler.execute(
                session_key,
                "SELECT missing FROM orders",
                purpose="统计订单",
                tool_call_id="call-2",
            )

        self.assertIs(raised.exception.result, validation)
        runtime.create_executor.assert_not_awaited()
        execution_service.execute.assert_not_awaited()
        _, kwargs = runtime.record_failure.await_args
        self.assertEqual(kwargs["status"], "rejected")
        self.assertEqual(kwargs["error_code"], "sql_validation_failed")
        self.assertEqual(kwargs["validation"], validation)


if __name__ == "__main__":
    unittest.main()
