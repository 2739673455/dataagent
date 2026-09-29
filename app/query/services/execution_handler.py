"""解析查询身份、校验 SQL 并执行只读查询。"""

from uuid import UUID

from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.credential import DorisCredentialCipher
from app.identity.services.identity import IdentityService
from app.query.errors import QueryRejectedError
from app.query.models.execution import AnalysisQueryResult, QueryExecutionOptions
from app.query.repositories.doris import DorisQueryRepository
from app.query.services.executor import AnalysisQueryService
from app.query.services.guard import QueryGuardService
from app.sandbox import DockerSandboxManager
from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg


class QueryExecutionHandler:
    """在短身份会话结束后执行 Doris 查询并保存产物。"""

    def __init__(
        self,
        artifact_store: DockerSandboxManager,
        auth: PostgresClientManager,
        query_clients: DorisQueryClientRegistry,
    ) -> None:
        self._artifact_store = artifact_store
        self._auth = auth
        self._query_clients = query_clients
        self._credential_cipher = DorisCredentialCipher(
            cfg.doris_credentials.encryption_key.get_secret_value()
        )

    async def execute(
        self,
        user_id: int,
        conversation_id: UUID,
        sql: str,
        *,
        purpose: str,
    ) -> AnalysisQueryResult:
        """校验并执行一次只读查询，返回结果或抛出原始错误。"""
        async with self._auth.session() as session, session.begin():
            principal = await IdentityService(
                IdentityPGRepo(session)
            ).get_query_principal(user_id, self._credential_cipher)
        validation = QueryGuardService().check(sql)
        if not validation.valid or validation.normalized_sql is None:
            raise QueryRejectedError(validation)
        client = self._query_clients.get_or_create(
            principal.role_name, principal.query_user, principal.password
        )
        service = AnalysisQueryService(
            DorisQueryRepository(client), self._artifact_store, QueryExecutionOptions()
        )
        return await service.execute(
            user_id, conversation_id, validation.normalized_sql, purpose=purpose
        )
