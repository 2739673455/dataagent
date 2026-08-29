import importlib
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.query.models.execution import AnalysisQueryResult
from app.query.models.validation import QueryValidationResult
from app.query.providers import build_query_execution_handler
from app.query.services.executor import QueryPlanEstimate, SuccessfulQueryExecution
from app.query.services.principal import ResolvedQueryPrincipal
from app.shared.contracts.analysis import AgentSessionKey

provider_module = importlib.import_module("app.query.providers")


class QueryProvidersTest(unittest.IsolatedAsyncioTestCase):
    async def test_server_selected_profile_builds_query_limits(self) -> None:
        session = MagicMock(spec=AsyncSession)

        @asynccontextmanager
        async def session_scope():
            yield session

        principal = ResolvedQueryPrincipal(
            role_name="dataagent_standard",
            authorization_epoch=uuid4(),
            query_user="standard_readonly",
            password="query_password",
            workload_group="dataagent_standard",
        )
        session_key = AgentSessionKey(
            user_id=7,
            conversation_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            analysis_id="analysis",
            agent_type="explorer",
            session_id="query",
        )
        query_result = AnalysisQueryResult(
            path="/analyses/a/sessions/explorer/s/query.csv",
            schema=[],
            row_count=0,
            time_range={},
            sample=[],
        )
        details = SuccessfulQueryExecution(
            session_key=session_key,
            raw_sql="SELECT 1",
            normalized_sql="SELECT 1",
            validation=QueryValidationResult(valid=True, normalized_sql="SELECT 1"),
            plan_estimate=QueryPlanEstimate(
                scan_nodes=0,
                scan_rows=0,
                scan_bytes=0,
            ),
            result=query_result,
        )
        execution_service = MagicMock()
        execution_service.execute = AsyncMock(return_value=details)
        experience_service = MagicMock()
        experience_service.record_success = AsyncMock()
        connection_provider = MagicMock()

        with (
            patch.object(
                provider_module.auth_postgres_client_manager,
                "session",
                new=session_scope,
            ),
            patch.object(
                provider_module.meta_postgres_client_manager,
                "session",
                new=session_scope,
            ),
            patch.object(
                provider_module.QueryPrincipalService,
                "resolve",
                new=AsyncMock(return_value=principal),
            ) as resolve,
            patch.object(
                provider_module.query_doris_client_registry,
                "get_or_create",
                new=AsyncMock(return_value=connection_provider),
            ) as get_pool,
            patch.object(
                provider_module,
                "AnalysisQueryService",
                return_value=execution_service,
            ) as service_type,
            patch.object(
                provider_module,
                "build_query_experience_service",
                return_value=experience_service,
            ),
        ):
            handler = build_query_execution_handler(MagicMock())
            result = await handler.execute(
                session_key,
                "SELECT 1",
                purpose="统计订单",
                tool_call_id="call-1",
            )

        resolve.assert_awaited_once_with(7)
        get_pool.assert_awaited_once_with(
            "dataagent_standard",
            "standard_readonly",
            "query_password",
        )
        limits = service_type.call_args.args[3]
        self.assertEqual(limits.workload_group, "dataagent_standard")
        self.assertEqual(result, query_result)


if __name__ == "__main__":
    unittest.main()
