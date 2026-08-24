"""字段语义索引访问"""

from typing import Any, ClassVar, cast

from elasticsearch import AsyncElasticsearch

from app.metadata.models import (
    ColumnInfo,
    ColumnKey,
    column_resource_key,
)
from app.metadata.repositories.semantic_index import SemanticIndexDeltaRepo
from app.metadata.search_models import (
    SearchHit,
    SemanticIndexDelta,
    SemanticIndexDocument,
    SemanticTextType,
)
from app.shared.config.app_config import cfg


class ColumnESRepo:
    """字段全文与向量索引存储"""

    _index_name = cfg.elasticsearch.column_index
    _exact_text_boosts: ClassVar[dict[SemanticTextType, float]] = {
        "name": 8.0,
        "alias": 6.0,
        "description": 4.0,
    }
    _index_mappings: ClassVar[dict[str, Any]] = {
        "dynamic": False,
        "properties": {
            "resource_key": {"type": "keyword"},
            "t_name": {"type": "keyword"},
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
        """初始化字段语义索引存储"""
        self._client = client
        self._delta_repo = SemanticIndexDeltaRepo(
            client,
            self._index_name,
            "字段语义索引",
        )

    async def ensure_index(self) -> None:
        """确保字段语义索引存在"""
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
        *,
        t_name: str,
        c_name: str,
    ) -> list[SemanticIndexDocument]:
        """读取字段当前语义索引文档并兼容识别旧文档"""
        return await self._delta_repo.list_documents(
            {
                "bool": {
                    "should": [
                        {"term": {"resource_key": resource_key}},
                        {
                            "bool": {
                                "filter": [
                                    {"term": {"t_name": t_name}},
                                    {"term": {"name": c_name}},
                                ]
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            }
        )

    async def apply_delta(self, delta: SemanticIndexDelta) -> None:
        """应用字段语义索引差量"""
        await self._delta_repo.apply_delta(delta)

    async def delete(self, t_name: str, c_name: str) -> None:
        """删除字段对应的全部语义索引文档"""
        await self._delete_by_filter(
            [
                {
                    "bool": {
                        "should": [
                            {
                                "term": {
                                    "resource_key": column_resource_key(t_name, c_name)
                                }
                            },
                            {
                                "bool": {
                                    "filter": [
                                        {"term": {"t_name": t_name}},
                                        {"term": {"name": c_name}},
                                    ]
                                }
                            },
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
        allowed_columns: frozenset[ColumnKey] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[ColumnInfo]]:
        """根据向量检索字段并保留命中分数"""
        result = await self._vector_search(
            embedding,
            score_threshold,
            limit,
            allowed_columns,
        )
        return self._hits(result)

    async def search_text_hits(
        self,
        query: str,
        *,
        allowed_columns: frozenset[ColumnKey] | None,
        limit: int = 5,
    ) -> list[SearchHit[ColumnInfo]]:
        """根据关键词检索字段并保留命中分数"""
        result = await self._text_search(query, limit, allowed_columns)
        return self._hits(result)

    async def _delete_by_filter(self, filters: list[dict[str, Any]]) -> None:
        """按过滤条件删除字段语义索引文档"""
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
        allowed_columns: frozenset[ColumnKey] | None,
    ) -> dict[str, Any]:
        """执行字段向量检索"""
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": embedding,
            "k": limit,
            "num_candidates": min(10_000, max(100, limit * 10)),
            "similarity": score_threshold,
        }
        if allowed_columns is not None:
            knn["filter"] = self._column_filter(allowed_columns)
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
        allowed_columns: frozenset[ColumnKey] | None,
    ) -> dict[str, Any]:
        """执行字段全文检索"""
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
        if allowed_columns is not None:
            text_query = {
                "bool": {
                    "must": [text_query],
                    "filter": [self._column_filter(allowed_columns)],
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
    def _column_filter(allowed_columns: frozenset[ColumnKey]) -> dict[str, Any]:
        """构造表字段联合白名单过滤条件"""
        if not allowed_columns:
            raise ValueError("allowed_columns 列表不能为空")
        return {
            "terms": {
                "resource_key": [
                    column_resource_key(t_name, c_name)
                    for t_name, c_name in sorted(allowed_columns)
                ]
            }
        }

    @staticmethod
    def _hits(result: dict[str, Any]) -> list[SearchHit[ColumnInfo]]:
        """将 Elasticsearch 命中转换为字段结果"""
        return [
            SearchHit(
                item=ColumnInfo(**hit["_source"]["payload"]),
                score=float(hit.get("_score") or 0.0),
            )
            for hit in result["hits"]["hits"]
            if isinstance(hit.get("_source", {}).get("payload"), dict)
        ]
