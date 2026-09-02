"""查询应用服务依赖组装。"""

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
from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.contracts import QueryExperienceIndexScheduler
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.execution_recorder import (
    QueryExecutionContext,
    QueryExecutionRecorder,
)
from app.query.services.executor import (
    AnalysisQueryService,
    QueryArtifactStore,
    SuccessfulQueryExecution,
)
from app.query.services.experience_indexer import QueryExperienceIndexer
from app.query.services.experience_invalidation import (
    QueryExperienceInvalidationService,
)
from app.query.services.experience_recall import QueryExperienceRecallService
from app.query.services.guard import QueryGuardService
from app.query.task_scheduler import query_experience_index_scheduler
from app.shared.clients.doris_client_manager import query_doris_client_registry
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg


def build_query_execution_recorder(
    session: AsyncSession,
    *,
    index_scheduler: QueryExperienceIndexScheduler = query_experience_index_scheduler,
) -> QueryExecutionRecorder:
    """创建查询执行记录与经验聚合服务。"""
    return QueryExecutionRecorder(
        execution_repo=QueryExecutionPGRepo(session),
        experience_repo=QueryExperiencePGRepo(session),
        index_scheduler=index_scheduler,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


def build_query_experience_recall_service(
    session: AsyncSession,
    *,
    index_scheduler: QueryExperienceIndexScheduler = query_experience_index_scheduler,
) -> QueryExperienceRecallService:
    """创建查询经验混合召回服务。"""
    return QueryExperienceRecallService(
        repo=QueryExperiencePGRepo(session),
        index_repo=QueryExperienceESRepo(client=es_client_manager.get_client()),
        embedding_client=embedding_client_manager.get_client(),
        index_scheduler=index_scheduler,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


def build_query_experience_indexer(session: AsyncSession) -> QueryExperienceIndexer:
    """创建查询经验索引同步服务。"""
    return QueryExperienceIndexer(
        repo=QueryExperiencePGRepo(session),
        index_repo=QueryExperienceESRepo(es_client_manager.get_client()),
        embedding_client=embedding_client_manager.get_client(),
    )


def build_query_experience_invalidation_service(
    session: AsyncSession,
    *,
    index_scheduler: QueryExperienceIndexScheduler = query_experience_index_scheduler,
) -> QueryExperienceInvalidationService:
    """创建不依赖 Elasticsearch 和 Embedding 的查询经验失效服务。"""
    return QueryExperienceInvalidationService(
        repo=QueryExperiencePGRepo(session=session),
        index_scheduler=index_scheduler,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


class DefaultQueryExecutionRuntime:
    """使用阶段化短会话提供查询用例运行环境。"""

    def __init__(self, artifact_store: QueryArtifactStore) -> None:
        """绑定查询产物存储和静态执行配置。"""
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
        """在单个认证会话中解析身份和资产策略。"""
        async with auth_postgres_client_manager.session() as session:
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
        """创建仅持有 Doris 和产物存储依赖的执行器。"""
        limits = QueryExecutionLimits(
            workload_group=principal.workload_group,
            timeout_seconds=cfg.query.timeout_seconds,
            memory_limit_bytes=cfg.query.memory_limit_bytes,
        )
        connection_provider = await query_doris_client_registry.get_or_create(
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
        async with meta_postgres_client_manager.session() as session:
            await build_query_execution_recorder(session).record_success(
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
        async with meta_postgres_client_manager.session() as session:
            await build_query_execution_recorder(session).record_failure(
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
    """组装身份解析、受控执行和历史记录完整查询用例。"""
    return QueryExecutionHandler(DefaultQueryExecutionRuntime(artifact_store))
