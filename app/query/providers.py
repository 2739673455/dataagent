"""查询应用服务依赖组装"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.repositories.auth import AuthPGRepo
from app.identity.repositories.query_identity import DorisQueryIdentityPGRepo
from app.identity.services.authorization import AuthorizationService
from app.identity.services.credential import DorisCredentialCipher
from app.metadata.repositories.postgres import MetaPGRepo
from app.query.models.execution import QueryExecutionLimits, QueryExecutionOptions
from app.query.repositories.doris import DorisQueryRepository
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.contracts import QueryExperienceIndexScheduler
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.executor import AnalysisQueryService, QueryArtifactStore
from app.query.services.experience import QueryExperienceService
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


def build_query_execution_handler(
    artifact_store: QueryArtifactStore,
) -> QueryExecutionHandler:
    """组装身份解析、受控执行和历史记录完整查询用例"""
    credential_cipher = DorisCredentialCipher(
        cfg.doris_credentials.encryption_key.get_secret_value()
    )
    options = QueryExecutionOptions(
        batch_size=cfg.query.batch_size,
        sample_rows=cfg.query.sample_rows,
    )

    def build_principal_service(session: AsyncSession) -> QueryPrincipalService:
        """使用认证会话构造查询身份解析服务"""
        return QueryPrincipalService(
            AuthPGRepo(session),
            DorisQueryIdentityPGRepo(session),
            credential_cipher,
        )

    async def build_execution_service(
        auth_session: AsyncSession,
        meta_session: AsyncSession,
        principal: ResolvedQueryPrincipal,
    ) -> AnalysisQueryService:
        """使用当前用户查询身份构造受控查询执行服务"""
        connection_provider = await query_doris_client_registry.get_or_create(
            principal.role_name,
            principal.query_user,
            principal.password,
        )
        guard = QueryGuardService(
            MetaPGRepo(meta_session),
            data_source=cfg.query.data_source,
            current_database=cfg.doris.database,
            policy_provider=AuthorizationService(AuthPGRepo(auth_session)),
        )
        limits = QueryExecutionLimits(
            workload_group=principal.workload_group,
            timeout_seconds=cfg.query.timeout_seconds,
            memory_limit_bytes=cfg.query.memory_limit_bytes,
            max_rows=cfg.query.max_rows,
            max_output_bytes=cfg.query.max_output_bytes,
        )
        return AnalysisQueryService(
            guard,
            DorisQueryRepository(connection_provider),
            artifact_store,
            limits,
            options,
        )

    return QueryExecutionHandler(
        auth_postgres_client_manager.session,
        meta_postgres_client_manager.session,
        build_principal_service,
        build_execution_service,
        build_query_experience_service,
    )
