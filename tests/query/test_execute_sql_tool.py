import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from langchain.tools import ToolRuntime
from pydantic import BaseModel

from app.analytics.agents.explorer.tools.execute_sql import create_execute_sql_tool
from app.query.models.execution import AnalysisQueryResult
from app.query.models.validation import QueryValidationIssue, QueryValidationResult
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.executor import QueryRejectedError


def make_runtime() -> ToolRuntime:
    """构造数据查询工具运行上下文"""
    return ToolRuntime(
        state={"messages": []},
        context=None,
        config={
            "configurable": {
                "user_id": 7,
                "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
                "analysis_id": "analysis",
                "session_id": "query",
            }
        },
        stream_writer=lambda _: None,
        tool_call_id="query-call",
        store=None,
    )


class ExecuteSqlToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_tool_context_to_query_handler(self) -> None:
        query_result = AnalysisQueryResult(
            path="/analyses/a/sessions/explorer/s/query.csv",
            schema=[],
            row_count=0,
            time_range={},
            sample=[],
        )
        handler = MagicMock(spec=QueryExecutionHandler)
        handler.execute = AsyncMock(return_value=query_result)
        tool = create_execute_sql_tool(handler)

        schema = cast(type[BaseModel], tool.tool_call_schema)
        self.assertEqual(set(schema.model_fields), {"sql", "purpose"})
        result = await cast(Any, tool).coroutine(
            runtime=make_runtime(),
            sql="SELECT 1",
        )

        handler.execute.assert_awaited_once()
        session_key, sql = handler.execute.await_args.args
        self.assertEqual(session_key.user_id, 7)
        self.assertEqual(
            session_key.conversation_id,
            UUID("550e8400-e29b-41d4-a716-446655440000"),
        )
        self.assertEqual(sql, "SELECT 1")
        self.assertEqual(
            handler.execute.await_args.kwargs,
            {
                "purpose": "执行只读数据查询",
                "tool_call_id": "query-call",
            },
        )
        self.assertEqual(result["status"], "success")

    async def test_validation_failure_returns_actionable_error(self) -> None:
        validation = QueryValidationResult(
            valid=False,
            normalized_sql=None,
            issues=[
                QueryValidationIssue(
                    code="unknown_column",
                    message="Unknown column: orders.missing_amount",
                    table="orders",
                    column="missing_amount",
                )
            ],
        )
        handler = MagicMock(spec=QueryExecutionHandler)
        handler.execute = AsyncMock(side_effect=QueryRejectedError(validation))
        tool = create_execute_sql_tool(handler)

        result = await cast(Any, tool).coroutine(
            runtime=make_runtime(),
            sql="SELECT missing_amount FROM orders",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "sql_validation_failed")
        self.assertIn("execute_sql", result["hint"])
        self.assertEqual(
            result["validation"]["issues"][0]["code"],
            "unknown_column",
        )

    async def test_unexpected_failure_includes_exception_detail(self) -> None:
        handler = MagicMock(spec=QueryExecutionHandler)
        handler.execute = AsyncMock(side_effect=RuntimeError("查询身份暂不可用"))
        tool = create_execute_sql_tool(handler)

        result = await cast(Any, tool).coroutine(
            runtime=make_runtime(),
            sql="SELECT 1",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "readonly_query_failed")
        self.assertEqual(
            result["details"],
            [{"type": "RuntimeError", "msg": "查询身份暂不可用"}],
        )


if __name__ == "__main__":
    unittest.main()
