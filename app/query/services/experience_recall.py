"""权限感知的查询经验混合召回。"""

import asyncio
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from loguru import logger

from app.identity.services.authorization import AssetAccessPolicy
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.query.models.experience import QueryExperience
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.contracts import QueryExperienceIndexScheduler
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.config.app_config import cfg
from app.shared.contracts.query_experience import (
    QueryAssetKind,
    QueryAssetSnapshot,
    QueryExperienceRecall,
    QueryExperienceRecallResult,
    QueryExperienceRecallStatus,
)
from app.shared.contracts.search import SearchHit

_SEARCH_POOL_SIZE = 100
_RRF_K = 60


@dataclass(frozen=True, slots=True)
class _SemanticRecall:
    """查询经验索引通道的内部融合结果。"""

    status: QueryExperienceRecallStatus
    ranks: dict[UUID, float]


class QueryExperienceRecallService:
    """检索经过当前权限和元数据版本复核的查询经验。"""

    def __init__(
        self,
        repo: QueryExperiencePGRepo,
        index_repo: QueryExperienceESRepo,
        embedding_client: EmbeddingClient,
        index_scheduler: QueryExperienceIndexScheduler,
        *,
        data_source: str,
        database_name: str,
    ) -> None:
        """绑定经验存储、混合索引和失效调度依赖。"""
        self._repo = repo
        self._index_repo = index_repo
        self._embedding_client = embedding_client
        self._index_scheduler = index_scheduler
        self._data_source = data_source
        self._database_name = database_name

    async def recall(
        self,
        *,
        role_name: str,
        authorization_epoch: UUID,
        policy: AssetAccessPolicy,
        query: str,
        limit: int,
    ) -> QueryExperienceRecall:
        """按混合语义排名检索查询经验。"""
        semantic_recall = await self._semantic_recall(
            query,
            role_name=role_name,
            authorization_epoch=authorization_epoch,
        )
        if semantic_recall.status == "failed":
            return QueryExperienceRecall(status="failed", results=[])
        semantic_ranks = semantic_recall.ranks
        async with self._repo.session.begin():
            experiences = await self._repo.get_many(
                list(semantic_ranks),
                role_name=role_name,
                authorization_epoch=authorization_epoch,
            )
            current_versions = await self._repo.current_asset_versions(experiences)
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
            invalid_revisions.update(
                await self._repo.disable_for_metadata_change(stale_ids)
            )
            experiences = [
                experience
                for experience in experiences
                if experience.id not in invalid_revisions
            ]
        for experience_id, revision in invalid_revisions.items():
            self._index_scheduler.enqueue(experience_id, revision)
        authorization_filter = MetadataAuthorizationFilter(
            policy,
            self._data_source,
            self._database_name,
        )
        ordered_experiences = sorted(
            experiences,
            key=lambda item: (-semantic_ranks[item.id], item.id.hex),
        )
        results = [
            result
            for experience in ordered_experiences
            if (result := self._to_recall_result(experience, authorization_filter))
            is not None
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
        authorization_epoch: UUID,
    ) -> _SemanticRecall:
        """分别召回全文和向量候选，并融合可用通道。"""
        text_task = asyncio.create_task(
            self._index_repo.search_text(
                query,
                role_name=role_name,
                authorization_epoch=authorization_epoch,
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
                    authorization_epoch=authorization_epoch,
                    limit=_SEARCH_POOL_SIZE,
                    min_score=cfg.query.query_experience_vector_score_threshold,
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

    @staticmethod
    def _to_recall_result(
        experience: QueryExperience,
        authorization_filter: MetadataAuthorizationFilter,
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
        if not authorization_filter.query_experience_is_allowed(assets):
            return None
        return QueryExperienceRecallResult(
            id=experience.id,
            purpose=experience.purposes[-1],
            sql_template=experience.sql_template,
            assets=assets,
        )
