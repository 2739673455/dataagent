"""只读查询完整用例编排。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.identity.application import IdentityService
from app.metadata.application import MetadataReader
from app.query.application.index_tasks import query_experience_index_scheduler
from app.query.contracts import (
    AnalysisQueryResult,
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryExecutionStatus,
    QueryValidationResult,
)
from app.query.errors import QueryRejectedError, classify_query_error
from app.query.repositories.doris import DorisQueryRepository
from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.execution_recorder import (
    QueryExecutionContext,
    QueryExecutionRecorder,
)
from app.query.services.executor import AnalysisQueryService
from app.query.services.guard import QueryGuardService
from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.contracts.analysis import AgentSessionKey

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.sandbox.application import DockerSandboxManager


class QueryExecutionService:
    """在独立短会话中解析身份、校验 SQL 和记录历史，Doris 执行不持有 PostgreSQL 会话。"""

    def __init__(
        self,
        *,
        identity: IdentityService,
        metadata: MetadataReader,
        postgres: PostgresClientManager,
        query_clients: DorisQueryClientRegistry,
        artifact_store: DockerSandboxManager,
    ) -> None:
        """绑定查询用例所需的身份、目录、查询客户端和产物存储。"""
        self._identity = identity
        self._metadata = metadata
        self._postgres = postgres
        self._query_clients = query_clients
        self._artifact_store = artifact_store
        self._guard = QueryGuardService(
            metadata.query_catalog, current_database=cfg.doris.database
        )
        self._options = QueryExecutionOptions(
            batch_size=cfg.query.batch_size,
            sample_rows=cfg.query.sample_rows,
        )

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
            principal = await self._identity.resolve_query_principal(
                session_key.user_id
            )
            context = QueryExecutionContext(
                session_key=session_key,
                role_name=principal.role_name,
                authorization_fingerprint=principal.authorization_fingerprint,
                purpose=purpose,
                tool_call_id=tool_call_id,
            )
            validation = await self._guard.check(sql)
            if not validation.valid or validation.normalized_sql is None:
                raise QueryRejectedError(validation)
            connection_provider = await self._query_clients.get_or_create(
                principal.role_name,
                principal.query_user,
                principal.password,
            )
            result = await AnalysisQueryService(
                DorisQueryRepository(connection_provider),
                self._artifact_store,
                QueryExecutionLimits(
                    workload_group=principal.workload_group,
                    timeout_seconds=cfg.query.timeout_seconds,
                    memory_limit_bytes=cfg.query.memory_limit_bytes,
                ),
                self._options,
            ).execute(
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
            async with self._postgres.session() as session:
                await self._recorder(session).record_success(
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
            async with self._postgres.session() as session:
                await self._recorder(session).record_failure(
                    context,
                    raw_sql=raw_sql,
                    status=status,
                    error_code=error_code,
                    error_detail=error_detail,
                    validation=validation,
                )
        except Exception:  # noqa: BLE001
            logger.exception("记录失败查询历史失败")

    def _recorder(self, session: AsyncSession) -> QueryExecutionRecorder:
        """使用本次查询会话组装执行记录与经验聚合能力。"""
        return QueryExecutionRecorder(
            execution_repo=QueryExecutionPGRepo(session),
            experience_repo=QueryExperiencePGRepo(session),
            index_scheduler=query_experience_index_scheduler,
            metadata=self._metadata,
            data_source=cfg.query.data_source,
            database_name=cfg.doris.database,
        )
