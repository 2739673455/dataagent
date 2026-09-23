"""只读查询完整用例编排。"""

from app.query.errors import QueryRejectedError
from app.query.models.execution import (
    AnalysisQueryResult,
)
from app.query.runtime import DatabaseQueryExecutionRuntime
from app.shared.contracts.analysis import AgentSessionKey


class QueryExecutionHandler:
    """解析查询身份并执行 SQL。"""

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
    ) -> AnalysisQueryResult:
        """校验并执行一次只读查询，返回结果或抛出原始错误。"""
        principal = await self._runtime.resolve_principal(session_key.user_id)
        validation = await self._runtime.validate(sql)
        if not validation.valid or validation.normalized_sql is None:
            raise QueryRejectedError(validation)
        service = await self._runtime.create_executor(principal)
        return await service.execute(
            session_key, validation.normalized_sql, purpose=purpose
        )
