"""只读查询完整用例编排。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.query.errors import QueryRejectedError, classify_query_error
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryExecutionStatus,
)
from app.query.models.validation import QueryValidationResult
from app.query.services.execution_recorder import QueryExecutionContext
from app.shared.contracts.analysis import AgentSessionKey

if TYPE_CHECKING:
    from app.query.runtime import DatabaseQueryExecutionRuntime


class QueryExecutionHandler:
    """解析查询身份、执行 SQL 并记录查询历史。"""

    def __init__(
        self,
        runtime: DatabaseQueryExecutionRuntime,
    ) -> None:
        """绑定查询用例运行环境。"""
        self._runtime = runtime

    async def execute(
        self,
        session_key: AgentSessionKey,
        sql: str,
        *,
        purpose: str,
        tool_call_id: str | None,
    ) -> AnalysisQueryResult:
        """执行一次只读查询并记录成功或失败事实。"""
        context: QueryExecutionContext | None = None
        validation: QueryValidationResult | None = None
        try:
            principal = await self._runtime.resolve_principal(session_key.user_id)
            context = QueryExecutionContext(
                session_key=session_key,
                role_name=principal.role_name,
                authorization_fingerprint=principal.authorization_fingerprint,
                purpose=purpose,
                tool_call_id=tool_call_id,
            )
            validation = await self._runtime.validate(sql)
            if not validation.valid or validation.normalized_sql is None:
                raise QueryRejectedError(validation)
            service = await self._runtime.create_executor(principal)
            result = await service.execute(
                session_key,
                validation,
                purpose=purpose,
            )
        except Exception as exc:
            status, error_code = classify_query_error(exc)
            await self._record_failure_safely(
                context,
                raw_sql=sql,
                status=status,
                error_code=error_code,
                error_detail=str(exc).strip() or "异常未提供详情",
                validation=exc.result
                if isinstance(exc, QueryRejectedError)
                else validation,
            )
            raise
        await self._record_success_safely(
            context, raw_sql=sql, validation=validation, result=result
        )
        return result

    async def _record_success_safely(
        self,
        context: QueryExecutionContext,
        *,
        raw_sql: str,
        validation: QueryValidationResult,
        result: AnalysisQueryResult,
    ) -> None:
        """记录成功查询，持久化故障不改变查询结果。"""
        try:
            await self._runtime.record_success(
                context, raw_sql=raw_sql, validation=validation, result=result
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
        """记录失败查询，持久化故障不覆盖原始错误。"""
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
