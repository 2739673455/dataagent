"""只读查询完整用例编排。"""

from typing import Protocol

from app.identity.services.authorization import AssetAccessPolicy
from app.identity.services.query_principal import ResolvedQueryPrincipal
from app.query.errors import QueryRejectedError
from app.query.models.execution import (
    AnalysisQueryResult,
)
from app.query.models.validation import QueryValidationResult
from app.query.services.executor import AnalysisQueryService
from app.shared.contracts.analysis import AgentSessionKey


class QueryExecutionRuntime(Protocol):
    """一次查询各阶段所需的短生命周期运行环境。"""

    async def resolve_principal(
        self,
        user_id: int,
    ) -> tuple[ResolvedQueryPrincipal, AssetAccessPolicy]:
        """解析查询身份和当前资产策略。"""
        ...

    async def validate(
        self,
        sql: str,
        policy: AssetAccessPolicy,
    ) -> QueryValidationResult:
        """在独立元数据会话中校验 SQL。"""
        ...

    async def create_executor(
        self,
        principal: ResolvedQueryPrincipal,
    ) -> AnalysisQueryService:
        """创建不持有 PostgreSQL 会话的 Doris 查询执行器。"""
        ...


class QueryExecutionHandler:
    """解析查询身份并执行 SQL。"""

    def __init__(
        self,
        runtime: QueryExecutionRuntime,
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
        principal, policy = await self._runtime.resolve_principal(session_key.user_id)
        validation = await self._runtime.validate(sql, policy)
        if not validation.valid or validation.normalized_sql is None:
            raise QueryRejectedError(validation)
        service = await self._runtime.create_executor(principal)
        return await service.execute(session_key, validation, purpose=purpose)
