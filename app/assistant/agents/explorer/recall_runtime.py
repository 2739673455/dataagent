"""Explorer Agent 语义召回运行时依赖。"""

from dataclasses import dataclass

from app.identity.providers import load_asset_policy
from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.metadata.providers import (
    build_semantic_resource_recall_service,
)
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.embedding_client_manager import EmbeddingClientManager
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager


@dataclass(frozen=True, slots=True)
class SemanticRecallRuntime:
    """召回能力使用的资源，由所属 Web 进程显式注入。"""

    auth: PostgresClientManager
    meta: PostgresClientManager
    embedding: EmbeddingClientManager
    es: ESClientManager
    doris: DorisClientManager

    async def search(
        self, user_id: int, request: SemanticResourceRecallRequest
    ) -> SemanticResourceRecallResponse:
        """取得本次策略和目录，再在读取会话之外执行外部检索。"""
        policy = await load_asset_policy(self.auth, self.doris, user_id)
        service = await build_semantic_resource_recall_service(
            self.meta, self.es.get_client(), self.embedding.get_client(), policy
        )
        return await service.recall(request)
