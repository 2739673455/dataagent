"""语义召回的授权、检索与记录用例。"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from elasticsearch import AsyncElasticsearch
from loguru import logger

from app.identity.providers import load_asset_policy
from app.identity.services.authorization import AssetAccessPolicy
from app.metadata.errors import SemanticRecallSaveError
from app.metadata.models.recall import (
    SemanticRecallRecord,
    SemanticRecallResourceDeletion,
)
from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.metadata.providers import (
    build_semantic_resource_recall_service,
    semantic_recall_context,
)
from app.metadata.services.recall import SemanticRecallContextService
from app.query.providers import build_query_experience_recall_service
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.embedding_client import EmbeddingClient
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.contracts.query_experience import (
    QUERY_EXPERIENCE_RECALL_LIMIT,
    QueryExperienceRecallResult,
)


@dataclass
class SemanticRecallService:
    """召回能力使用的资源，由所属 Web 进程显式注入。"""

    auth: PostgresClientManager
    meta: PostgresClientManager
    embedding: EmbeddingClient
    es: AsyncElasticsearch
    doris: DorisClientManager

    async def search(
        self, user_id: int, request: SemanticResourceRecallRequest
    ) -> tuple[AssetAccessPolicy, SemanticResourceRecallResponse]:
        """取得本次策略和目录，再在读取会话之外执行外部检索。"""
        policy = await load_asset_policy(self.auth, self.doris, user_id)
        service = await build_semantic_resource_recall_service(
            self.meta, self.es, self.embedding, policy
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

    async def query_experiences(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
        policy: AssetAccessPolicy,
    ) -> tuple[list[QueryExperienceRecallResult], datetime]:
        """复用有效快照；经验检索失败时保留语义召回并允许下次重试。"""
        try:
            async with self.context_service(user_id, policy=policy) as service:
                cached = await service.get_fresh_query_experiences(
                    user_id, conversation_id, query
                )
            if cached is not None:
                return cached
            if policy.role_name is None or policy.authorization_fingerprint is None:
                return [], datetime.now(UTC)
            async with self.meta.session() as session:
                result = await build_query_experience_recall_service(
                    session, self.es, self.embedding
                ).recall(
                    role_name=policy.role_name,
                    authorization_fingerprint=policy.authorization_fingerprint,
                    policy=policy,
                    query=query,
                    limit=QUERY_EXPERIENCE_RECALL_LIMIT,
                )
            if result.status != "failed":
                return result.results, datetime.now(UTC)
            logger.warning("查询经验全文和向量检索均不可用")
        except Exception:  # noqa: BLE001
            logger.exception("查询经验检索失败")
        return [], datetime.min.replace(tzinfo=UTC)

    async def recall_context(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
        request: SemanticResourceRecallRequest,
    ) -> SemanticRecallRecord:
        """共用本次授权快照，完成语义检索、经验召回和记录保存。"""
        policy, response = await self.search(user_id, request)
        experiences, retrieved_at = await self.query_experiences(
            user_id, conversation_id, query, policy
        )
        try:
            async with self.context_service(user_id, policy=policy) as service:
                return await service.record(
                    user_id,
                    conversation_id,
                    query,
                    request,
                    response,
                    experiences,
                    retrieved_at,
                )
        except Exception as exc:
            raise SemanticRecallSaveError("无法保存语义召回快照") from exc

    async def list_recalls(
        self, user_id: int, conversation_id: UUID, limit: int
    ) -> list[SemanticRecallRecord]:
        """按最新权限读取会话召回记录。"""
        async with self.context_service(user_id) as service:
            return await service.list(user_id, conversation_id, limit=limit)

    async def get_recall(
        self, user_id: int, conversation_id: UUID, query: str
    ) -> SemanticRecallRecord:
        """按最新权限读取指定记录。"""
        async with self.context_service(user_id) as service:
            return await service.get(user_id, conversation_id, query)

    async def merge_recalls(
        self, user_id: int, conversation_id: UUID, target_query: str, source_query: str
    ) -> SemanticRecallRecord:
        """在同一事务中合并已授权记录并删除来源。"""
        async with self.context_service(user_id) as service:
            return await service.merge(
                user_id, conversation_id, target_query, source_query
            )

    async def delete_recalls(
        self,
        user_id: int,
        conversation_id: UUID,
        deletions: list[SemanticRecallResourceDeletion],
    ) -> None:
        """在同一事务中删除指定上下文资源。"""
        async with self.context_service(user_id) as service:
            await service.delete(user_id, conversation_id, deletions)
