"""权限感知的查询经验混合召回。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import UUID

from loguru import logger

from app.identity.contracts import AssetAccessPolicy
from app.metadata import MetadataReader
from app.query.contracts import (
    QueryAssetKind,
    QueryAssetSnapshot,
    QueryExperienceRecall,
    QueryExperienceRecallResult,
    QueryExperienceRecallStatus,
)
from app.query.experiences.authorization import query_assets_are_allowed
from app.query.experiences.scheduler import CeleryQueryExperienceIndexScheduler
from app.query.models.experience import QueryExperience
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import QueryConfig
from app.shared.contracts.assets import asset_resource_key
from app.shared.contracts.search import SearchHit

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch

    from app.shared.clients.embedding_client import EmbeddingClient

_SEARCH_POOL_SIZE = 100
_RRF_K = 60


@dataclass(frozen=True, slots=True)
class _SemanticRecall:
    """查询经验索引通道的内部融合结果。"""

    status: QueryExperienceRecallStatus
    ranks: dict[UUID, float]


class QueryExperienceService:
    """在独立短会话中检索并复核当前权限与元数据版本内的查询经验。"""

    def __init__(
        self,
        postgres: PostgresClientManager,
        metadata: MetadataReader,
        es: AsyncElasticsearch,
        embedding: EmbeddingClient,
        *,
        config: QueryConfig,
        database_name: str,
        index_scheduler: CeleryQueryExperienceIndexScheduler,
    ) -> None:
        """绑定经验存储、混合索引和失效调度依赖。"""
        self._config = config
        self._index_scheduler = index_scheduler
        self._postgres = postgres
        self._index_repo = QueryExperienceESRepo(es)
        self._embedding_client = embedding
        self._metadata = metadata
        self._data_source = self._config.data_source
        self._database_name = database_name

    async def recall(
        self,
        policy: AssetAccessPolicy,
        query: str,
        limit: int,
    ) -> QueryExperienceRecall:
        """按混合语义排名检索查询经验。"""
        if policy.role_name is None or policy.authorization_fingerprint is None:
            return QueryExperienceRecall(status="success", results=[])
        semantic_recall = await self._semantic_recall(
            query,
            role_name=policy.role_name,
            authorization_fingerprint=policy.authorization_fingerprint,
        )
        if semantic_recall.status == "failed":
            return QueryExperienceRecall(status="failed", results=[])
        semantic_ranks = semantic_recall.ranks
        async with self._postgres.session() as session, session.begin():
            experiences = await QueryExperiencePGRepo(session).get_many(
                list(semantic_ranks),
                role_name=policy.role_name,
                authorization_fingerprint=policy.authorization_fingerprint,
            )
        versions = await self._metadata.asset_versions(
            {
                asset.table_name
                for item in experiences
                for asset in item.assets
                if asset.kind == "table"
            },
            {
                (asset.table_name, asset.column_name)
                for item in experiences
                for asset in item.assets
                if asset.kind == "column" and asset.column_name is not None
            },
        )
        current_versions = {
            asset_resource_key(self._data_source, self._database_name, table): version
            for table, version in versions.tables.items()
        }
        current_versions.update(
            {
                asset_resource_key(
                    self._data_source, self._database_name, table, column
                ): version
                for (table, column), version in versions.columns.items()
            }
        )
        async with self._postgres.session() as session, session.begin():
            repo = QueryExperiencePGRepo(session)
            experiences = await repo.get_many(
                list(semantic_ranks),
                role_name=policy.role_name,
                authorization_fingerprint=policy.authorization_fingerprint,
            )
            invalid_revisions = {
                experience.id: experience.revision
                for experience in experiences
                if experience.status != "active"
            }
            stale_ids = {
                experience.id
                for experience in experiences
                if experience.status == "active"
                and any(
                    current_versions.get(asset.resource_key) != asset.meta_version
                    for asset in experience.assets
                )
            }
            invalid_revisions.update(await repo.disable_for_metadata_change(stale_ids))
            experiences = [
                experience
                for experience in experiences
                if experience.id not in invalid_revisions
            ]
        for experience_id, revision in invalid_revisions.items():
            self._index_scheduler.enqueue(experience_id, revision)
        ordered_experiences = sorted(
            experiences,
            key=lambda item: (-semantic_ranks[item.id], item.id.hex),
        )
        results = [
            result
            for experience in ordered_experiences
            if (result := self._to_recall_result(experience, policy)) is not None
        ][:limit]
        return QueryExperienceRecall(
            status=semantic_recall.status,
            results=results,
        )

    async def _semantic_recall(
        self,
        query: str,
        *,
        role_name: str,
        authorization_fingerprint: str,
    ) -> _SemanticRecall:
        """分别召回全文和向量候选，并融合可用通道。"""
        text_task = asyncio.create_task(
            self._index_repo.search_text(
                query,
                role_name=role_name,
                authorization_fingerprint=authorization_fingerprint,
                limit=_SEARCH_POOL_SIZE,
            )
        )
        vector_task: asyncio.Task[list[SearchHit[UUID]]] | None = None
        try:
            embedding = (await self._embedding_client.aembed_documents([query]))[0]
            vector_task = asyncio.create_task(
                self._index_repo.search_vector(
                    embedding,
                    role_name=role_name,
                    authorization_fingerprint=authorization_fingerprint,
                    limit=_SEARCH_POOL_SIZE,
                    min_score=self._config.query_experience_vector_score_threshold,
                )
            )
        except asyncio.CancelledError:
            text_task.cancel()
            await asyncio.gather(text_task, return_exceptions=True)
            raise
        except Exception:  # noqa: BLE001
            logger.exception("查询经验向量生成失败")

        text_hits = await self._await_hits(text_task, "全文")
        vector_hits = (
            await self._await_hits(vector_task, "向量")
            if vector_task is not None
            else None
        )
        available_hits = [hits for hits in (text_hits, vector_hits) if hits is not None]
        if not available_hits:
            return _SemanticRecall(status="failed", ranks={})
        ranks: dict[UUID, float] = {}
        for hits in available_hits:
            for rank, hit in enumerate(hits, start=1):
                ranks[hit.item] = ranks.get(hit.item, 0) + 1 / (_RRF_K + rank)
        return _SemanticRecall(
            status="success" if len(available_hits) == 2 else "partial",
            ranks=ranks,
        )

    @staticmethod
    async def _await_hits(
        task: asyncio.Task[list[SearchHit[UUID]]],
        channel: str,
    ) -> list[SearchHit[UUID]] | None:
        """等待单个检索通道，保留另一路的结果。"""
        try:
            return await task
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(f"查询经验{channel}检索失败")
            return None

    def _to_recall_result(
        self,
        experience: QueryExperience,
        policy: AssetAccessPolicy,
    ) -> QueryExperienceRecallResult | None:
        """将已通过有效性检查的经验转换为模型可用结果。"""
        assets = [
            QueryAssetSnapshot(
                kind=cast(QueryAssetKind, asset.kind),
                database=asset.database_name,
                table=asset.table_name,
                column=asset.column_name,
                meta_version=asset.meta_version,
            )
            for asset in sorted(
                experience.assets,
                key=lambda item: (
                    item.kind,
                    item.table_name,
                    item.column_name or "",
                ),
            )
        ]
        if not query_assets_are_allowed(
            assets, policy, self._data_source, self._database_name
        ):
            return None
        return QueryExperienceRecallResult(
            id=experience.id,
            purpose=experience.purposes[-1],
            sql_template=experience.sql_template,
            assets=assets,
        )
