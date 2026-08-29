"""查询应用服务依赖组装"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.repositories.auth import AuthPGRepo
from app.identity.repositories.query_identity import DorisQueryIdentityPGRepo
from app.identity.services.authorization import AssetAccessPolicy, AuthorizationService
from app.identity.services.credential import DorisCredentialCipher
from app.metadata.repositories.postgres import MetaPGRepo
from app.query.models.execution import (
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryExecutionStatus,
)
from app.query.models.validation import QueryValidationResult
from app.query.repositories.doris import DorisQueryRepository
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.executor import (
    AnalysisQueryService,
    QueryArtifactStore,
    SuccessfulQueryExecution,
)
from app.query.services.experience import (
    QueryExecutionContext,
    QueryExperienceIndexScheduler,
    QueryExperienceService,
)
from app.query.services.guard import QueryGuardService
from app.query.services.principal import QueryPrincipalService, ResolvedQueryPrincipal
from app.query.task_scheduler import query_experience_index_scheduler
from app.shared.clients.doris_client_manager import query_doris_client_registry
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg


def build_query_experience_service(
    session: AsyncSession,
    *,
    index_scheduler: QueryExperienceIndexScheduler = query_experience_index_scheduler,
) -> QueryExperienceService:
    """创建查询经验记录、检索与索引维护服务"""
    return QueryExperienceService(
        repo=QueryExperiencePGRepo(session=session),
        index_repo=QueryExperienceESRepo(client=es_client_manager.get_client()),
        embedding_client=embedding_client_manager.get_client(),
        index_scheduler=index_scheduler,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


class DefaultQueryExecutionRuntime:
    """使用阶段化短会话提供查询用例运行环境"""

    def __init__(self, artifact_store: QueryArtifactStore) -> None:
        """绑定查询产物存储和静态执行配置"""
        self._artifact_store = artifact_store
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
        """在单个认证会话中解析身份和资产策略"""
        async with auth_postgres_client_manager.session() as session:
            auth_repo = AuthPGRepo(session)
            principal = await QueryPrincipalService(
                auth_repo,
                DorisQueryIdentityPGRepo(session),
                self._credential_cipher,
            ).resolve(user_id)
            policy = await AuthorizationService(auth_repo).get_asset_policy(user_id)
        return principal, policy

    async def validate(
        self,
        sql: str,
        policy: AssetAccessPolicy,
    ) -> QueryValidationResult:
        """在独立元数据会话中校验 SQL"""
        async with meta_postgres_client_manager.session() as session:
            return await QueryGuardService(
                MetaPGRepo(session),
                data_source=cfg.query.data_source,
                current_database=cfg.doris.database,
            ).check(sql, policy)

    async def create_executor(
        self,
        principal: ResolvedQueryPrincipal,
    ) -> AnalysisQueryService:
        """创建仅持有 Doris 和产物存储依赖的执行器"""
        connection_provider = await query_doris_client_registry.get_or_create(
            principal.role_name,
            principal.query_user,
            principal.password,
        )
        limits = QueryExecutionLimits(
            workload_group=principal.workload_group,
            timeout_seconds=cfg.query.timeout_seconds,
            memory_limit_bytes=cfg.query.memory_limit_bytes,
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
        """使用独立元数据会话记录成功事实"""
        async with meta_postgres_client_manager.session() as session:
            await build_query_experience_service(session).record_success(
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
        """使用独立元数据会话记录失败事实"""
        async with meta_postgres_client_manager.session() as session:
            await build_query_experience_service(session).record_failure(
                context,
                raw_sql=raw_sql,
                status=status,
                error_code=error_code,
                error_detail=error_detail,
                validation=validation,
            )


def build_query_execution_handler(
    artifact_store: QueryArtifactStore,
) -> QueryExecutionHandler:
    """组装身份解析、受控执行和历史记录完整查询用例"""
    return QueryExecutionHandler(DefaultQueryExecutionRuntime(artifact_store))
