"""Explorer Agent 语义召回运行时依赖。"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from langchain_core.runnables import RunnableConfig

from app.identity.providers import load_asset_policy
from app.identity.services.authorization import AssetAccessPolicy
from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.metadata.providers import (
    build_semantic_resource_recall_service,
    semantic_recall_context,
)
from app.metadata.services.recall import SemanticRecallContextService
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.embedding_client_manager import EmbeddingClientManager
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager


def resolve_semantic_recall_identity(
    config: RunnableConfig,
) -> tuple[int, UUID]:
    """从服务端运行配置解析会话身份。"""
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    raw_conversation_id = configurable.get("conversation_id")
    if not isinstance(user_id, int) or not isinstance(raw_conversation_id, str):
        raise TypeError("配置中未找到语义召回上下文")
    return user_id, UUID(raw_conversation_id)


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
    ) -> tuple[AssetAccessPolicy, SemanticResourceRecallResponse]:
        """取得本次策略和目录，再在读取会话之外执行外部检索。"""
        policy = await load_asset_policy(self.auth, self.doris, user_id)
        service = await build_semantic_resource_recall_service(
            self.meta, self.es.get_client(), self.embedding.get_client(), policy
        )
        return policy, await service.recall(request)

    @asynccontextmanager
    async def context_service(
        self, user_id: int, *, policy: AssetAccessPolicy | None = None
    ) -> AsyncGenerator[SemanticRecallContextService]:
        """单次操作内复用策略，独立工具/消息读取始终重新授权。"""
        if policy is None:
            policy = await load_asset_policy(self.auth, self.doris, user_id)
        async with semantic_recall_context(self.meta, policy) as service:
            yield service
