"""查询经验全文与向量索引访问。"""

from typing import Any, ClassVar, cast
from uuid import UUID

from elasticsearch import AsyncElasticsearch, ConflictError, NotFoundError

from app.metadata.models.search import SearchHit
from app.shared.config.app_config import cfg


class QueryExperienceESRepo:
    """查询经验用途文本的混合检索索引。"""

    _index_name = cfg.elasticsearch.query_experience_index
    _index_mappings: ClassVar[dict[str, Any]] = {
        "dynamic": False,
        "properties": {
            "role_name": {"type": "keyword"},
            "authorization_epoch": {"type": "keyword"},
            "text": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_max_word",
            },
            "embedding": {
                "type": "dense_vector",
                "dims": cfg.elasticsearch.embedding_size,
                "index": True,
                "similarity": "cosine",
                "index_options": {"type": "hnsw"},
            },
        },
    }

    def __init__(self, client: AsyncElasticsearch) -> None:
        """绑定查询经验使用的 Elasticsearch 客户端。"""
        self._client = client

    async def ensure_index(self) -> None:
        """确保查询经验索引存在。"""
        if not await self._client.indices.exists(index=self._index_name):
            await self._client.indices.create(
                index=self._index_name,
                mappings=self._index_mappings,
            )

    async def index(
        self,
        experience_id: UUID,
        *,
        revision: int,
        role_name: str,
        authorization_epoch: UUID,
        text: str,
        embedding: list[float],
    ) -> None:
        """按外部版本顺序覆盖查询经验索引文档。"""
        await self.ensure_index()
        try:
            await self._client.index(
                index=self._index_name,
                id=str(experience_id),
                document={
                    "role_name": role_name,
                    "authorization_epoch": str(authorization_epoch),
                    "text": text,
                    "embedding": embedding,
                },
                version=revision,
                version_type="external_gte",
                refresh="wait_for",
            )
        except ConflictError:
            return

    async def delete(self, experience_id: UUID, *, revision: int) -> None:
        """按外部版本顺序删除查询经验索引文档。"""
        await self.ensure_index()
        try:
            await self._client.delete(
                index=self._index_name,
                id=str(experience_id),
                version=revision,
                version_type="external_gte",
                refresh="wait_for",
            )
        except (ConflictError, NotFoundError):
            return

    async def search_text(
        self,
        query: str,
        *,
        role_name: str,
        authorization_epoch: UUID,
        limit: int,
    ) -> list[SearchHit[UUID]]:
        """按任务文本执行全文检索。"""
        if not await self._client.indices.exists(index=self._index_name):
            return []
        result = await self._client.search(
            index=self._index_name,
            query={
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["text^2"],
                                "type": "best_fields",
                            }
                        }
                    ],
                    "filter": self._scope_filter(role_name, authorization_epoch),
                }
            },
            size=limit,
        )
        return self._hits(result)

    async def search_vector(
        self,
        embedding: list[float],
        *,
        role_name: str,
        authorization_epoch: UUID,
        limit: int,
        min_score: float,
    ) -> list[SearchHit[UUID]]:
        """按任务向量执行语义检索。"""
        if not await self._client.indices.exists(index=self._index_name):
            return []
        result = await self._client.search(
            index=self._index_name,
            knn={
                "field": "embedding",
                "query_vector": embedding,
                "k": limit,
                "num_candidates": min(10_000, max(100, limit * 10)),
                "filter": {
                    "bool": {
                        "filter": self._scope_filter(role_name, authorization_epoch),
                    }
                },
            },
            size=limit,
            min_score=min_score,
        )
        return self._hits(result)

    @staticmethod
    def _scope_filter(
        role_name: str,
        authorization_epoch: UUID,
    ) -> list[dict[str, Any]]:
        """构造角色和权限纪元一致的索引过滤条件。"""
        return [
            {"term": {"role_name": role_name}},
            {"term": {"authorization_epoch": str(authorization_epoch)}},
        ]

    @staticmethod
    def _hits(result: Any) -> list[SearchHit[UUID]]:
        """解析 Elasticsearch 查询经验命中。"""
        payload = cast(dict[str, Any], result.body)
        hits: list[SearchHit[UUID]] = []
        for hit in payload.get("hits", {}).get("hits", []):
            try:
                experience_id = UUID(str(hit["_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            hits.append(
                SearchHit(
                    item=experience_id,
                    score=float(hit.get("_score") or 0.0),
                )
            )
        return hits
