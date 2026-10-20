"""语义召回用例编排。"""

from dataclasses import dataclass

from elasticsearch import AsyncElasticsearch

from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.metadata.providers import (
    build_semantic_resource_recall_service,
)
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.clients.postgres_client_manager import PostgresClientManager


@dataclass(frozen=True, slots=True)
class SemanticRecallHandler:
    """读取完整元数据目录并执行语义召回。"""

    meta: PostgresClientManager
    embedding: EmbeddingClient
    es: AsyncElasticsearch

    async def search(
        self, request: SemanticResourceRecallRequest
    ) -> SemanticResourceRecallResponse:
        """取得完整目录，再在读取会话之外执行外部检索。"""
        service = await build_semantic_resource_recall_service(
            self.meta, self.es, self.embedding
        )
        return await service.recall(request)
