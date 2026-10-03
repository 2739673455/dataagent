"""只读查询完整用例编排。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.identity import IdentityService
from app.metadata import MetadataReader
from app.query.contracts import (
    AnalysisQueryResult,
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryExecutionScope,
    QueryExecutionStatus,
    QueryValidationResult,
)
from app.query.errors import QueryRejectedError, classify_query_error
from app.query.execution.executor import AnalysisQueryService
from app.query.execution.guard import QueryGuardService
from app.query.execution.recorder import (
    QueryExecutionContext,
    QueryExecutionRecorder,
)
from app.query.experiences.scheduler import CeleryQueryExperienceIndexScheduler
from app.query.repositories.doris import DorisQueryRepository
from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import QueryConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.sandbox import DockerSandboxManager


class QueryExecutionService:
    """使用短数据库会话解析身份、校验 SQL 和记录历史；Doris 查询在会话释放后执行。"""

    def __init__(
        self,
        *,
        identity: IdentityService,
        metadata: MetadataReader,
        postgres: PostgresClientManager,
        query_clients: DorisQueryClientRegistry,
        artifact_store: DockerSandboxManager,
        config: QueryConfig,
        database_name: str,
        index_scheduler: CeleryQueryExperienceIndexScheduler,
    ) -> None:
        """绑定查询用例所需的身份、目录、查询客户端和产物存储。"""
        self._config = config
        self._database_name = database_name
        self._index_scheduler = index_scheduler
        self._identity = identity
        self._metadata = metadata
        self._postgres = postgres
        self._query_clients = query_clients
        self._artifact_store = artifact_store
        self._guard = QueryGuardService(
            metadata.query_catalog, current_database=self._database_name
        )
        self._options = QueryExecutionOptions(
            batch_size=self._config.batch_size,
            sample_rows=self._config.sample_rows,
        )

    async def execute(
        self,
        session_key: QueryExecutionScope,
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
            normalized_sql = validation.normalized_sql
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
                    timeout_seconds=self._config.timeout_seconds,
                    memory_limit_bytes=self._config.memory_limit_bytes,
                ),
                self._options,
            ).execute(
                session_key,
                normalized_sql,
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
            context,
            raw_sql=sql,
            normalized_sql=normalized_sql,
            validation=validation,
            result=result,
        )
        return result

    async def _record_success_safely(
        self,
        context: QueryExecutionContext,
        *,
        raw_sql: str,
        normalized_sql: str,
        validation: QueryValidationResult,
        result: AnalysisQueryResult,
    ) -> None:
        """记录成功查询，审计写入失败时记录日志并保留查询结果。"""
        try:
            async with self._postgres.session() as session:
                await self._recorder(session).record_success(
                    context,
                    raw_sql=raw_sql,
                    normalized_sql=normalized_sql,
                    validation=validation,
                    result=result,
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
        """记录失败查询，审计写入失败时记录日志并保留查询原始错误。"""
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
            index_scheduler=self._index_scheduler,
            metadata=self._metadata,
            data_source=self._config.data_source,
            database_name=self._database_name,
        )
