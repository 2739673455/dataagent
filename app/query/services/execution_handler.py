"""只读查询完整用例编排"""

from typing import Protocol

from loguru import logger

from app.identity.services.authorization import AssetAccessPolicy
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryExecutionStatus,
    QueryExecutionTimeoutError,
)
from app.query.models.validation import QueryValidationResult
from app.query.services.executor import (
    AnalysisQueryService,
    QueryPlanUnavailableError,
    QueryRejectedError,
    QueryResultShapeError,
    SuccessfulQueryExecution,
)
from app.query.services.experience import QueryExecutionContext
from app.query.services.principal import ResolvedQueryPrincipal
from app.shared.contracts.analysis import AgentSessionKey


class QueryExecutionRuntime(Protocol):
    """一次查询各阶段所需的短生命周期运行环境"""

    async def resolve_principal(
        self,
        user_id: int,
    ) -> tuple[ResolvedQueryPrincipal, AssetAccessPolicy]:
        """解析查询身份和当前资产策略"""
        ...

    async def validate(
        self,
        sql: str,
        policy: AssetAccessPolicy,
    ) -> QueryValidationResult:
        """在独立元数据会话中校验 SQL"""
        ...

    async def create_executor(
        self,
        principal: ResolvedQueryPrincipal,
    ) -> AnalysisQueryService:
        """创建不持有 PostgreSQL 会话的 Doris 查询执行器"""
        ...

    async def record_success(
        self,
        context: QueryExecutionContext,
        details: SuccessfulQueryExecution,
    ) -> None:
        """在独立元数据会话中记录成功事实"""
        ...

    async def record_failure(
        self,
        context: QueryExecutionContext,
        *,
        raw_sql: str,
        status: QueryExecutionStatus,
        error_code: str,
        error_detail: str,
        validation: QueryValidationResult | None = None,
    ) -> None:
        """在独立元数据会话中记录失败事实"""
        ...


class QueryExecutionHandler:
    """解析查询身份、执行 SQL 并记录查询历史"""

    def __init__(
        self,
        runtime: QueryExecutionRuntime,
    ) -> None:
        """绑定查询用例运行环境"""
        self._runtime = runtime

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
            principal, policy = await self._runtime.resolve_principal(
                session_key.user_id
            )
            context = QueryExecutionContext(
                session_key=session_key,
                role_name=principal.role_name,
                authorization_epoch=principal.authorization_epoch,
                purpose=purpose,
                tool_call_id=tool_call_id,
            )
            validation = await self._runtime.validate(sql, policy)
            if not validation.valid or validation.normalized_sql is None:
                raise QueryRejectedError(validation)
            service = await self._runtime.create_executor(principal)
            details = await service.execute(session_key, sql, validation)
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
            QueryPlanUnavailableError,
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
            await self._runtime.record_success(context, details)
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
            await self._runtime.record_failure(
                context,
                raw_sql=raw_sql,
                status=status,
                error_code=error_code,
                error_detail=error_detail,
                validation=validation,
            )
        except Exception:  # noqa: BLE001
            logger.exception("记录失败查询历史失败")
