"""查询执行审计。"""

from dataclasses import dataclass

from app.query.models.execution import (
    AnalysisQueryResult,
    QueryExecution,
    QueryExecutionStatus,
)
from app.query.models.validation import QueryValidationResult
from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.shared.contracts.analysis import AgentSessionKey


@dataclass(frozen=True, slots=True)
class QueryExecutionContext:
    """SQL 工具提供的用户、角色和任务上下文。"""

    session_key: AgentSessionKey
    role_name: str
    authorization_fingerprint: str
    purpose: str
    tool_call_id: str | None = None


class QueryExecutionRecorder:
    """记录查询执行审计。"""

    def __init__(self, execution_repo: QueryExecutionPGRepo) -> None:
        """绑定执行审计存储。"""
        self._execution_repo = execution_repo

    async def record_success(
        self,
        context: QueryExecutionContext,
        *,
        raw_sql: str,
        validation: QueryValidationResult,
        result: AnalysisQueryResult,
    ) -> None:
        """记录成功执行及结果摘要。"""
        normalized_sql = validation.normalized_sql
        if not validation.valid or normalized_sql is None:
            raise ValueError("成功执行记录必须使用有效的 SQL 校验结果")
        execution = self._new_execution(context, raw_sql, "succeeded")
        execution.normalized_sql = normalized_sql
        execution.validation = validation.model_dump(mode="json")
        execution.result_summary = self._result_summary(result)
        await self._execution_repo.record(execution)

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
        """记录被 Guard 拒绝或执行失败的 SQL。"""
        execution = self._new_execution(context, raw_sql, status)
        execution.error_code = error_code
        execution.error_detail = error_detail[:4000]
        if validation is not None:
            execution.normalized_sql = validation.normalized_sql
            execution.validation = validation.model_dump(mode="json")
        await self._execution_repo.record(execution)

    @staticmethod
    def _new_execution(
        context: QueryExecutionContext,
        raw_sql: str,
        status: QueryExecutionStatus,
    ) -> QueryExecution:
        """构造三个记录分支共用的执行上下文字段。"""
        return QueryExecution(
            user_id=context.session_key.user_id,
            role_name=context.role_name,
            authorization_fingerprint=context.authorization_fingerprint,
            conversation_id=context.session_key.conversation_id,
            analysis_id=context.session_key.analysis_id,
            session_id=context.session_key.session_id,
            tool_call_id=context.tool_call_id,
            purpose=context.purpose,
            raw_sql=raw_sql,
            status=status,
        )

    @staticmethod
    def _result_summary(result: AnalysisQueryResult) -> dict[str, object]:
        """构造成功执行的持久化结果摘要。"""
        return {
            "path": result.path,
            "columns": [item.model_dump(mode="json") for item in result.columns],
            "row_count": result.row_count,
            "time_range": {
                key: value.model_dump(mode="json")
                for key, value in result.time_range.items()
            },
        }
