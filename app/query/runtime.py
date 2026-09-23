"""查询各阶段的数据库运行环境；每个阶段使用独立短会话。"""

from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.credential import DorisCredentialCipher
from app.identity.services.query_principal import (
    QueryPrincipalService,
    ResolvedQueryPrincipal,
)
from app.query.models.execution import (
    QueryExecutionOptions,
)
from app.query.models.validation import QueryValidationResult
from app.query.repositories.doris import DorisQueryRepository
from app.query.services.executor import (
    AnalysisQueryService,
)
from app.query.services.guard import QueryGuardService
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.doris_client_manager import (
    DorisQueryClientRegistry,
)
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg


class DatabaseQueryExecutionRuntime:
    """使用阶段化短会话提供查询用例运行环境。"""

    def __init__(
        self,
        artifact_store: DockerSandboxManager,
        auth: PostgresClientManager,
        query_clients: DorisQueryClientRegistry,
    ) -> None:
        """绑定查询产物存储和静态执行配置。"""
        self._auth = auth
        self._query_clients = query_clients
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
    ) -> ResolvedQueryPrincipal:
        """在单个认证会话中解析查询身份。"""
        async with self._auth.session() as session, session.begin():
            repo = IdentityPGRepo(session)
            return await QueryPrincipalService(
                repo,
                self._credential_cipher,
            ).resolve(user_id)

    async def validate(
        self,
        sql: str,
    ) -> QueryValidationResult:
        """在本地检查 SQL 只读语法。"""
        return QueryGuardService().check(sql)

    async def create_executor(
        self,
        principal: ResolvedQueryPrincipal,
    ) -> AnalysisQueryService:
        """创建仅持有 Doris 和产物存储依赖的执行器。"""
        connection_provider = await self._query_clients.get_or_create(
            principal.role_name,
            principal.query_user,
            principal.password,
        )
        return AnalysisQueryService(
            DorisQueryRepository(connection_provider),
            self._artifact_store,
            self._options,
        )
