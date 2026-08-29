"""只读查询完整用例编排"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.query.models.execution import AnalysisQueryResult, QueryExecutionStatus
from app.query.models.validation import QueryValidationResult
from app.query.services.executor import (
    AnalysisQueryService,
    QueryExecutionTimeoutError,
    QueryOutputLimitExceededError,
    QueryPlanUnavailableError,
    QueryRejectedError,
    QueryResultLimitExceededError,
    QueryResultShapeError,
    SuccessfulQueryExecution,
)
from app.query.services.experience import QueryExecutionContext, QueryExperienceService
from app.query.services.principal import QueryPrincipalService, ResolvedQueryPrincipal
from app.shared.contracts.analysis import AgentSessionKey

type AsyncSessionFactory = Callable[
    [], AbstractAsyncContextManager[AsyncSession]
]
type QueryPrincipalServiceFactory = Callable[[AsyncSession], QueryPrincipalService]
type AnalysisQueryServiceFactory = Callable[
    [AsyncSession, AsyncSession, ResolvedQueryPrincipal],
    Awaitable[AnalysisQueryService],
]
type QueryExperienceServiceFactory = Callable[[AsyncSession], QueryExperienceService]


class QueryExecutionHandler:
    """解析查询身份、执行 SQL 并记录查询历史"""

    def __init__(
        self,
        auth_session_factory: AsyncSessionFactory,
        meta_session_factory: AsyncSessionFactory,
        principal_service_factory: QueryPrincipalServiceFactory,
        execution_service_factory: AnalysisQueryServiceFactory,
        experience_service_factory: QueryExperienceServiceFactory,
    ) -> None:
        """绑定查询用例所需的会话和业务服务工厂"""
        self._auth_session_factory = auth_session_factory
        self._meta_session_factory = meta_session_factory
        self._principal_service_factory = principal_service_factory
        self._execution_service_factory = execution_service_factory
        self._experience_service_factory = experience_service_factory

    async def execute(
        self,
        session_key: AgentSessionKey,
        sql: str,
        *,
        purpose: str,
        tool_call_id: str | None,
    ) -> AnalysisQueryResult:
        """执行一次只读查询并记录成功或失败事实"""
        context: QueryExecutionContext | None = None
        try:
            async with (
                self._auth_session_factory() as auth_session,
                self._meta_session_factory() as meta_session,
            ):
                principal = await self._principal_service_factory(
                    auth_session
                ).resolve(session_key.user_id)
                context = QueryExecutionContext(
                    session_key=session_key,
                    role_name=principal.role_name,
                    authorization_epoch=principal.authorization_epoch,
                    purpose=purpose,
                    tool_call_id=tool_call_id,
                )
                service = await self._execution_service_factory(
                    auth_session,
                    meta_session,
                    principal,
                )
                details = await service.execute(session_key, sql)
        except QueryRejectedError as exc:
            await self._record_failure_safely(
                context,
                raw_sql=sql,
                status="rejected",
                error_code="sql_validation_failed",
                error_detail=str(exc),
                validation=exc.result,
            )
            raise
        except (
            QueryExecutionTimeoutError,
            QueryOutputLimitExceededError,
            QueryPlanUnavailableError,
            QueryResultLimitExceededError,
            QueryResultShapeError,
        ) as exc:
            await self._record_failure_safely(
                context,
                raw_sql=sql,
                status="failed",
                error_code="query_result_rejected",
                error_detail=str(exc),
            )
            raise
        except Exception as exc:
            await self._record_failure_safely(
                context,
                raw_sql=sql,
                status="failed",
                error_code="readonly_query_failed",
                error_detail=str(exc).strip() or "异常未提供详情",
            )
            raise
        await self._record_success_safely(context, details)
        return details.result

    async def _record_success_safely(
        self,
        context: QueryExecutionContext,
        details: SuccessfulQueryExecution,
    ) -> None:
        """记录成功查询，持久化故障不改变查询结果"""
        try:
            async with self._meta_session_factory() as session:
                await self._experience_service_factory(session).record_success(
                    context,
                    details,
                )
        except Exception:  # noqa: BLE001
            logger.exception("记录成功查询历史失败")

    async def _record_failure_safely(
        self,
        context: QueryExecutionContext | None,
        *,
        raw_sql: str,
        status: QueryExecutionStatus,
        error_code: str,
        error_detail: str,
        validation: QueryValidationResult | None = None,
    ) -> None:
        """记录失败查询，持久化故障不覆盖原始错误"""
        if context is None:
            return
        try:
            async with self._meta_session_factory() as session:
                await self._experience_service_factory(session).record_failure(
                    context,
                    raw_sql=raw_sql,
                    status=status,
                    error_code=error_code,
                    error_detail=error_detail,
                    validation=validation,
                )
        except Exception:  # noqa: BLE001
            logger.exception("记录失败查询历史失败")
