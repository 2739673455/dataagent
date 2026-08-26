"""指标语义索引访问"""

from typing import Any, ClassVar, cast

from elasticsearch import AsyncElasticsearch

from app.metadata.models.catalog import MetricInfo
from app.metadata.models.search import (
    SearchHit,
    SemanticIndexDelta,
    SemanticIndexDocument,
    SemanticTextType,
)
from app.metadata.repositories.semantic_index import SemanticIndexDeltaRepo
from app.shared.config.app_config import cfg


class MetricESRepo:
    """指标全文与向量索引存储"""

    _index_name = cfg.elasticsearch.metric_index
    _exact_text_boosts: ClassVar[dict[SemanticTextType, float]] = {
        "name": 8.0,
        "alias": 6.0,
        "description": 4.0,
    }
    _index_mappings: ClassVar[dict[str, Any]] = {
        "dynamic": False,
        "properties": {
            "resource_key": {"type": "keyword"},
            "name": {"type": "keyword"},
            "text": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_max_word",
                "fields": {
                    "raw": {
                        "type": "keyword",
                        "ignore_above": 1024,
                    }
                },
            },
            "text_type": {"type": "keyword"},
            "meta_version": {"type": "long"},
            "embedding_revision": {"type": "keyword"},
            "payload_hash": {"type": "keyword"},
            "embedding": {
                "type": "dense_vector",
                "dims": cfg.elasticsearch.embedding_size,
                "index": True,
                "similarity": "cosine",
                "index_options": {"type": "hnsw"},
            },
            "payload": {"type": "object", "enabled": False},
        },
    }

    def __init__(self, client: AsyncElasticsearch) -> None:
        """初始化指标语义索引存储"""
        self._client = client
        self._delta_repo = SemanticIndexDeltaRepo(
            client,
            self._index_name,
            "指标语义索引",
        )

    async def ensure_index(self) -> None:
        """确保指标语义索引存在"""
        if await self._client.indices.exists(index=self._index_name):
            await self._client.indices.put_mapping(
                index=self._index_name,
                properties={
                    "resource_key": {"type": "keyword"},
                    "meta_version": {"type": "long"},
                    "embedding_revision": {"type": "keyword"},
                    "payload_hash": {"type": "keyword"},
                },
            )
        else:
            await self._client.indices.create(
                index=self._index_name,
                mappings=self._index_mappings,
            )

    async def list_resource_documents(
        self,
        resource_key: str,
    ) -> list[SemanticIndexDocument]:
        """读取指标当前语义索引文档并兼容识别旧文档"""
        return await self._delta_repo.list_documents(
            {
                "bool": {
                    "should": [
                        {"term": {"resource_key": resource_key}},
                        {"term": {"name": resource_key}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )

    async def apply_delta(self, delta: SemanticIndexDelta) -> None:
        """应用指标语义索引差量"""
        await self._delta_repo.apply_delta(delta)

    async def delete(self, metric_name: str) -> None:
        """删除指标对应的全部语义索引文档"""
        await self._delete_by_filter(
            [
                {
                    "bool": {
                        "should": [
                            {"term": {"resource_key": metric_name}},
                            {"term": {"name": metric_name}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            ]
        )

    async def search_vector_hits(
        self,
        embedding: list[float],
        *,
        allowed_metrics: frozenset[str] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[MetricInfo]]:
        """根据向量检索指标并保留命中分数"""
        result = await self._vector_search(
            embedding,
            score_threshold,
            limit,
            allowed_metrics,
        )
        return self._hits(result)

    async def search_text_hits(
        self,
        query: str,
        *,
        allowed_metrics: frozenset[str] | None,
        limit: int = 5,
    ) -> list[SearchHit[MetricInfo]]:
        """根据关键词检索指标并保留命中分数"""
        result = await self._text_search(query, limit, allowed_metrics)
        return self._hits(result)

    async def _delete_by_filter(self, filters: list[dict[str, Any]]) -> None:
        """按过滤条件删除指标语义索引文档"""
        if not await self._client.indices.exists(index=self._index_name):
            return
        await self._client.delete_by_query(
            index=self._index_name,
            query={"bool": {"filter": filters}},
            conflicts="proceed",
            refresh=True,
        )

    async def _vector_search(
        self,
        embedding: list[float],
        score_threshold: float,
        limit: int,
        allowed_metrics: frozenset[str] | None,
    ) -> dict[str, Any]:
        """执行指标向量检索"""
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": embedding,
            "k": limit,
            "num_candidates": min(10_000, max(100, limit * 10)),
            "similarity": score_threshold,
        }
        if allowed_metrics is not None:
            knn["filter"] = self._metric_filter(allowed_metrics)
        result = await self._client.search(
            index=self._index_name,
            knn=knn,
            size=limit,
        )
        body = result.body if hasattr(result, "body") else result
        return cast(dict[str, Any], body)

    async def _text_search(
        self,
        query: str,
        limit: int,
        allowed_metrics: frozenset[str] | None,
    ) -> dict[str, Any]:
        """执行指标全文检索"""
        exact_queries = [
            {
                "bool": {
                    "filter": [{"term": {"text_type": text_type}}],
                    "must": [
                        {
                            "term": {
                                "text.raw": {
                                    "value": query,
                                    "case_insensitive": True,
                                }
                            }
                        }
                    ],
                    "boost": boost,
                }
            }
            for text_type, boost in self._exact_text_boosts.items()
        ]
        text_query: dict[str, Any] = {
            "dis_max": {
                "queries": [
                    *exact_queries,
                    {"match_phrase": {"text": {"query": query, "boost": 2.0}}},
                    {"match": {"text": query}},
                ]
            }
        }
        if allowed_metrics is not None:
            text_query = {
                "bool": {
                    "must": [text_query],
                    "filter": [self._metric_filter(allowed_metrics)],
                }
            }
        result = await self._client.search(
            index=self._index_name,
            query=text_query,
            size=limit,
        )
        body = result.body if hasattr(result, "body") else result
        return cast(dict[str, Any], body)

    @staticmethod
    def _metric_filter(allowed_metrics: frozenset[str]) -> dict[str, Any]:
        """构造指标名称白名单过滤条件"""
        if not allowed_metrics:
            raise ValueError("allowed_metrics 列表不能为空")
        names = sorted(allowed_metrics)
        return {
            "bool": {
                "should": [
                    {"terms": {"resource_key": names}},
                    {"terms": {"name": names}},
                ],
                "minimum_should_match": 1,
            }
        }

    @staticmethod
    def _hits(result: dict[str, Any]) -> list[SearchHit[MetricInfo]]:
        """将 Elasticsearch 命中转换为指标结果"""
        return [
            SearchHit(
                item=MetricInfo(**hit["_source"]["payload"]),
                score=float(hit.get("_score") or 0.0),
            )
            for hit in result["hits"]["hits"]
            if isinstance(hit.get("_source", {}).get("payload"), dict)
        ]
