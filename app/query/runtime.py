"""查询各阶段的数据库运行环境；每个阶段使用独立短会话。"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.authorization import AssetAccessPolicy, AuthorizationService
from app.identity.services.credential import DorisCredentialCipher
from app.identity.services.query_principal import (
    QueryPrincipalService,
    ResolvedQueryPrincipal,
)
from app.metadata.repositories.postgres import MetaPGRepo
from app.query.models.execution import (
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryExecutionStatus,
)
from app.query.models.validation import QueryValidationResult
from app.query.repositories.doris import DorisQueryRepository
from app.query.services.execution_recorder import (
    QueryExecutionContext,
    QueryExecutionRecorder,
)
from app.query.services.executor import (
    AnalysisQueryService,
    QueryArtifactStore,
    SuccessfulQueryExecution,
)
from app.query.services.guard import QueryGuardService
from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg


class DatabaseQueryExecutionRuntime:
    """使用阶段化短会话提供查询用例运行环境。"""

    def __init__(
        self,
        artifact_store: QueryArtifactStore,
        recorder_factory: Callable[[AsyncSession], QueryExecutionRecorder],
        auth: PostgresClientManager,
        meta: PostgresClientManager,
        query_clients: DorisQueryClientRegistry,
    ) -> None:
        """绑定查询产物存储和静态执行配置。"""
        self._auth = auth
        self._meta = meta
        self._query_clients = query_clients
        self._artifact_store = artifact_store
        self._recorder_factory = recorder_factory
        self._credential_cipher = DorisCredentialCipher(
            cfg.doris_credentials.encryption_key.get_secret_value()
        )
        self._options = QueryExecutionOptions(
            batch_size=cfg.query.batch_size,
            sample_rows=cfg.query.sample_rows,
        )

    async def resolve_principal(
        self,
        user_id: int,
    ) -> tuple[ResolvedQueryPrincipal, AssetAccessPolicy]:
        """在单个认证会话中解析身份和资产策略。"""
        async with self._auth.session() as session:
            repo = IdentityPGRepo(session)
            principal = await QueryPrincipalService(
                repo,
                self._credential_cipher,
            ).resolve(user_id)
            policy = await AuthorizationService(repo).get_asset_policy(user_id)
        return principal, policy

    async def validate(
        self,
        sql: str,
        policy: AssetAccessPolicy,
    ) -> QueryValidationResult:
        """在独立元数据会话中校验 SQL。"""
        async with self._meta.session() as session:
            return await QueryGuardService(
                MetaPGRepo(session),
                data_source=cfg.query.data_source,
                current_database=cfg.doris.database,
            ).check(sql, policy)

    async def create_executor(
        self,
        principal: ResolvedQueryPrincipal,
    ) -> AnalysisQueryService:
        """创建仅持有 Doris 和产物存储依赖的执行器。"""
        limits = QueryExecutionLimits(
            workload_group=principal.workload_group,
            timeout_seconds=cfg.query.timeout_seconds,
            memory_limit_bytes=cfg.query.memory_limit_bytes,
        )
        connection_provider = await self._query_clients.get_or_create(
            principal.role_name,
            principal.query_user,
            principal.password,
        )
        return AnalysisQueryService(
            DorisQueryRepository(connection_provider),
            self._artifact_store,
            limits,
            self._options,
        )

    async def record_success(
        self,
        context: QueryExecutionContext,
        details: SuccessfulQueryExecution,
    ) -> None:
        """使用独立元数据会话记录成功事实。"""
        async with self._meta.session() as session:
            await self._recorder_factory(session).record_success(
                context,
                details,
            )

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
        """使用独立元数据会话记录失败事实。"""
        async with self._meta.session() as session:
            await self._recorder_factory(session).record_failure(
                context,
                raw_sql=raw_sql,
                status=status,
                error_code=error_code,
                error_detail=error_detail,
                validation=validation,
            )
