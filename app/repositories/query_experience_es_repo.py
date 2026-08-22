"""查询经验全文与向量索引访问"""

from typing import Any, ClassVar, cast
from uuid import UUID

from elasticsearch import AsyncElasticsearch

from app.conf.app_config import cfg
from app.models.semantic_search import SearchHit


class QueryExperienceESRepo:
    """查询经验用途文本的混合检索索引"""

    _index_name = cfg.elasticsearch.query_experience_index
    _index_mappings: ClassVar[dict[str, Any]] = {
        "dynamic": False,
        "properties": {
            "owner_user_id": {"type": "long"},
            "role_name": {"type": "keyword"},
            "quality": {"type": "keyword"},
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
        self._client = client

    async def ensure_index(self) -> None:
        """确保查询经验索引存在"""
        if not await self._client.indices.exists(index=self._index_name):
            await self._client.indices.create(
                index=self._index_name,
                mappings=self._index_mappings,
            )

    async def index(
        self,
        experience_id: UUID,
        *,
        owner_user_id: int,
        role_name: str,
        quality: str,
        text: str,
        embedding: list[float],
    ) -> None:
        """覆盖写入一条查询经验索引文档"""
        await self.ensure_index()
        await self._client.index(
            index=self._index_name,
            id=str(experience_id),
            document={
                "owner_user_id": owner_user_id,
                "role_name": role_name,
                "quality": quality,
                "text": text,
                "embedding": embedding,
            },
            refresh="wait_for",
        )

    async def delete_many(self, experience_ids: list[UUID]) -> None:
        """删除指定查询经验的全部索引文档"""
        if not experience_ids or not await self._client.indices.exists(
            index=self._index_name
        ):
            return
        for offset in range(0, len(experience_ids), 1000):
            result = await self._client.delete_by_query(
                index=self._index_name,
                query={
                    "ids": {
                        "values": [
                            str(experience_id)
                            for experience_id in experience_ids[offset : offset + 1000]
                        ]
                    }
                },
                conflicts="proceed",
                refresh=True,
            )
            body = result.body if hasattr(result, "body") else result
            if isinstance(body, dict) and body.get("failures"):
                raise RuntimeError(
                    "Elasticsearch 删除查询经验存在失败项"
                )

    async def search_text(
        self,
        query: str,
        *,
        user_id: int,
        role_name: str,
        limit: int,
    ) -> list[SearchHit[UUID]]:
        """按任务文本执行全文检索"""
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
                    "filter": self._scope_filter(user_id, role_name),
                    "must_not": [{"term": {"quality": "disabled"}}],
                }
            },
            size=limit,
        )
        return self._hits(result)

    async def search_vector(
        self,
        embedding: list[float],
        *,
        user_id: int,
        role_name: str,
        limit: int,
    ) -> list[SearchHit[UUID]]:
        """按任务向量执行语义检索"""
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
                        "filter": self._scope_filter(user_id, role_name),
                        "must_not": [{"term": {"quality": "disabled"}}],
                    }
                },
            },
            size=limit,
        )
        return self._hits(result)

    @staticmethod
    def _scope_filter(user_id: int, role_name: str) -> list[dict[str, Any]]:
        """构造用户私有且角色一致的索引过滤条件"""
        return [
            {"term": {"owner_user_id": user_id}},
            {"term": {"role_name": role_name}},
        ]

    @staticmethod
    def _hits(result: Any) -> list[SearchHit[UUID]]:
        """解析 Elasticsearch 查询经验命中"""
        body = result.body if hasattr(result, "body") else result
        payload = cast(dict[str, Any], body)
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
