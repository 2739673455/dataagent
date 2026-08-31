import importlib
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.services.authorization import AssetAccessPolicy
from app.query.models.execution import AnalysisQueryResult
from app.query.models.validation import QueryValidationResult
from app.query.providers import (
    build_query_execution_handler,
    build_query_experience_invalidation_service,
)
from app.query.services.executor import QueryPlanEstimate, SuccessfulQueryExecution
from app.query.services.experience_invalidation import (
    QueryExperienceInvalidationService,
)
from app.query.services.principal import ResolvedQueryPrincipal
from app.shared.contracts.analysis import AgentSessionKey

provider_module = importlib.import_module("app.query.providers")


class QueryProvidersTest(unittest.IsolatedAsyncioTestCase):
    def test_invalidation_factory_does_not_require_search_clients(self) -> None:
        session = MagicMock(spec=AsyncSession)
        scheduler = MagicMock()

        service = build_query_experience_invalidation_service(
            session,
            index_scheduler=scheduler,
        )

        self.assertIsInstance(service, QueryExperienceInvalidationService)

    async def test_invalid_workload_group_is_rejected_before_client_creation(
        self,
    ) -> None:
        runtime = provider_module.DefaultQueryExecutionRuntime(MagicMock())
        principal = ResolvedQueryPrincipal(
            role_name="dataagent_standard",
            authorization_epoch=uuid4(),
            query_user="standard_readonly",
            password="query_password",
            workload_group="readonly'; DROP ROLE admin; --",
        )

        with (
            patch.object(
                provider_module.query_doris_client_registry,
                "get_or_create",
                new=AsyncMock(),
            ) as get_or_create,
            self.assertRaises(ValidationError),
        ):
            await runtime.create_executor(principal)

        get_or_create.assert_not_awaited()

    async def test_server_selected_profile_builds_query_limits(self) -> None:
        session = MagicMock(spec=AsyncSession)
        active_sessions = {"auth": 0, "meta": 0}

        @asynccontextmanager
        async def auth_session_scope():
            active_sessions["auth"] += 1
            try:
                yield session
            finally:
                active_sessions["auth"] -= 1

        @asynccontextmanager
        async def meta_session_scope():
            active_sessions["meta"] += 1
            try:
                yield session
            finally:
                active_sessions["meta"] -= 1

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
            path="/sessions/a/explorer/s/query.csv",
            columns=[],
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

        async def execute_query(*_: object) -> SuccessfulQueryExecution:
            self.assertEqual(active_sessions, {"auth": 0, "meta": 0})
            return details

        execution_service = MagicMock()
        execution_service.execute = AsyncMock(side_effect=execute_query)
        experience_service = MagicMock()
        experience_service.record_success = AsyncMock()
        connection_provider = MagicMock()

        with (
            patch.object(
                provider_module.auth_postgres_client_manager,
                "session",
                new=auth_session_scope,
            ),
            patch.object(
                provider_module.meta_postgres_client_manager,
                "session",
                new=meta_session_scope,
            ),
            patch.object(
                provider_module.QueryPrincipalService,
                "resolve",
                new=AsyncMock(return_value=principal),
            ) as resolve,
            patch.object(
                provider_module.AuthorizationService,
                "get_asset_policy",
                new=AsyncMock(return_value=AssetAccessPolicy(user_id=7)),
            ),
            patch.object(
                provider_module.QueryGuardService,
                "check",
                new=AsyncMock(
                    return_value=QueryValidationResult(
                        valid=True,
                        normalized_sql="SELECT 1",
                    )
                ),
            ),
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
        limits = service_type.call_args.args[2]
        self.assertEqual(limits.workload_group, "dataagent_standard")
        execution_service.execute.assert_awaited_once()
        self.assertEqual(active_sessions, {"auth": 0, "meta": 0})
        self.assertEqual(result, query_result)


if __name__ == "__main__":
    unittest.main()
