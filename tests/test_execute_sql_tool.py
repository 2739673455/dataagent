import importlib
import unittest
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from langchain.tools import ToolRuntime

from app.agents.explorer.tools.execute_sql import execute_sql
from app.conf.app_config import DorisRoleConfig
from app.entities.query import (
    AnalysisQueryResult,
    QueryValidationIssue,
    QueryValidationResult,
)
from app.services.query_guard_service import QueryRejectedError
from app.services.query_principal_service import ResolvedQueryPrincipal

tool_module = importlib.import_module("app.agents.explorer.tools.execute_sql")


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
                "agent_type": "explorer",
                "session_id": "query",
            }
        },
        stream_writer=lambda _: None,
        tool_call_id="query-call",
        store=None,
    )


class ExecuteSqlToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_server_selected_profile_controls_pool_and_workload_group(
        self,
    ) -> None:
        profile = DorisRoleConfig(
            description="标准角色",
            is_default=True,
            query_user="standard_readonly",
            query_password="query_password",
            workload_group="dataagent_standard",
        )
        principal = ResolvedQueryPrincipal(
            role_name="dataagent_standard",
            config=profile,
        )
        query_result = AnalysisQueryResult(
            path="/analyses/a/sessions/explorer/s/query.csv",
            schema=[],
            row_count=0,
            time_range={},
            sample=[],
        )
        session = MagicMock()

        @asynccontextmanager
        async def session_context():
            yield session

        service = MagicMock()
        service.execute = AsyncMock(return_value=query_result)
        provider = MagicMock()
        runtime = make_runtime()
        with (
            patch.object(
                tool_module.meta_postgres_client_manager,
                "session",
                new=session_context,
            ),
            patch.object(
                tool_module.QueryPrincipalService,
                "resolve",
                new=AsyncMock(return_value=principal),
            ) as resolve,
            patch.object(
                tool_module.query_doris_client_registry,
                "get",
                return_value=provider,
            ) as get_pool,
            patch.object(
                tool_module,
                "build_query_guard",
                return_value=MagicMock(),
            ),
            patch.object(
                tool_module,
                "AnalysisQueryService",
                return_value=service,
            ) as service_type,
        ):
            coroutine = cast(Any, execute_sql).coroutine
            result = await coroutine(runtime=runtime, sql="SELECT 1")

        resolve.assert_awaited_once_with(7)
        get_pool.assert_called_once_with("dataagent_standard")
        limits = service_type.call_args.args[3]
        self.assertEqual(limits.workload_group, "dataagent_standard")
        service.execute.assert_awaited_once()
        session_key = service.execute.await_args.args[0]
        self.assertEqual(session_key.user_id, 7)
        self.assertEqual(
            session_key.conversation_id,
            UUID("550e8400-e29b-41d4-a716-446655440000"),
        )
        self.assertEqual(result["status"], "success")

    async def test_validation_failure_returns_actionable_error_before_query(
        self,
    ) -> None:
        principal = ResolvedQueryPrincipal(
            role_name="dataagent_standard",
            config=DorisRoleConfig(
                description="标准角色",
                is_default=True,
                query_user="standard_readonly",
                query_password="query_password",
                workload_group="dataagent_standard",
            ),
        )
        validation = QueryValidationResult(
            valid=False,
            dialect="doris",
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
        service = MagicMock()
        service.execute = AsyncMock(side_effect=QueryRejectedError(validation))
        session = MagicMock()

        @asynccontextmanager
        async def session_context():
            yield session

        with (
            patch.object(
                tool_module.meta_postgres_client_manager,
                "session",
                new=session_context,
            ),
            patch.object(
                tool_module.QueryPrincipalService,
                "resolve",
                new=AsyncMock(return_value=principal),
            ),
            patch.object(tool_module.query_doris_client_registry, "get"),
            patch.object(tool_module, "build_query_guard", return_value=MagicMock()),
            patch.object(tool_module, "AnalysisQueryService", return_value=service),
        ):
            coroutine = cast(Any, execute_sql).coroutine
            result = await coroutine(
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


if __name__ == "__main__":
    unittest.main()
