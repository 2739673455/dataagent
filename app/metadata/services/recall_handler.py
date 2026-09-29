"""语义召回用例编排。"""

from dataclasses import dataclass

from elasticsearch import AsyncElasticsearch

from app.identity.providers import load_asset_policy
from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.metadata.providers import (
    build_semantic_resource_recall_service,
)
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.clients.postgres_client_manager import PostgresClientManager


@dataclass(frozen=True, slots=True)
class SemanticRecallHandler:
    """读取授权和元数据目录并执行语义召回。"""

    auth: PostgresClientManager
    meta: PostgresClientManager
    embedding: EmbeddingClient
    es: AsyncElasticsearch
    doris: DorisClientManager

    async def search(
        self, user_id: int, request: SemanticResourceRecallRequest
    ) -> SemanticResourceRecallResponse:
        """取得本次策略和目录，再在读取会话之外执行外部检索。"""
        policy = await load_asset_policy(self.auth, self.doris, user_id)
        service = await build_semantic_resource_recall_service(
            self.meta, self.es, self.embedding, policy
        )
        return await service.recall(request)
