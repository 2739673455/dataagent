"""语义索引写入与检索。"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar, cast

from elasticsearch import AsyncElasticsearch
from loguru import logger

from app.metadata.errors import CorruptedSemanticIndexDocumentError
from app.metadata.models.catalog import ColumnKey, column_resource_key
from app.metadata.models.search import (
    SemanticIndexDocument,
    SemanticTextType,
)
from app.shared.config.app_config import cfg
from app.shared.contracts.search import SearchHit
from app.shared.database.base import MetaBase

_EXACT_TEXT_BOOSTS: dict[SemanticTextType, float] = {
    "name": 8.0,
    "alias": 6.0,
    "description": 4.0,
}


def column_resource_terms_filter(
    allowed_keys: frozenset[ColumnKey],
) -> dict[str, Any]:
    """构造字段资源键白名单 Elasticsearch filter。"""
    return {
        "terms": {
            "resource_key": [
                column_resource_key(t_name, c_name)
                for t_name, c_name in sorted(allowed_keys)
            ]
        }
    }


class SemanticIndexRepo[ItemT: MetaBase, KeyT](ABC):
    """字段和指标索引共用的 Elasticsearch 技术实现。"""

    _index_name: ClassVar[str]
    _payload_type: type[ItemT]

    _index_mappings: ClassVar[dict[str, Any]] = {
        "dynamic": False,
        "properties": {
            "resource_key": {"type": "keyword"},
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
        """绑定 Elasticsearch 客户端，索引名称由子类定义。"""
        self._client = client

    async def reset_index(self) -> None:
        """删除当前索引并使用最新映射重建。"""
        await self._client.options(ignore_status=404).indices.delete(
            index=self._index_name
        )
        await self._client.indices.create(
            index=self._index_name, mappings=self._index_mappings
        )

    async def write_documents(
        self, documents: list[SemanticIndexDocument], *, batch_size: int = 100
    ) -> None:
        """分批写入完整语义文档，全部成功后刷新索引。"""
        for offset in range(0, len(documents), batch_size):
            operations: list[dict[str, Any]] = []
            for document in documents[offset : offset + batch_size]:
                operations.extend(
                    [
                        {"index": {"_index": self._index_name, "_id": document.id}},
                        {
                            "resource_key": document.resource_key,
                            "text": document.text,
                            "text_type": document.text_type,
                            "embedding": document.embedding,
                            "payload": document.payload,
                        },
                    ]
                )
            result = await self._client.bulk(operations=operations, refresh=False)
            payload = cast(dict[str, Any], result.body)
            if payload.get("errors"):
                failures = [
                    item
                    for item in payload.get("items", [])
                    if item["index"].get("error")
                ]
                raise RuntimeError(
                    f"Elasticsearch 语义索引写入失败: index={self._index_name}, errors={failures[:3]}"
                )
        if documents:
            await self._client.indices.refresh(index=self._index_name)

    async def search_text_hits(
        self,
        query: str,
        *,
        allowed_keys: frozenset[KeyT] | None,
        limit: int = 5,
    ) -> list[SearchHit[ItemT]]:
        """按资源白名单执行全文检索并解析命中。"""
        if allowed_keys is not None and not allowed_keys:
            return []
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
            for text_type, boost in _EXACT_TEXT_BOOSTS.items()
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
        if allowed_keys is not None:
            text_query = {
                "bool": {
                    "must": [text_query],
                    "filter": [self._resource_filter(allowed_keys)],
                }
            }
        result = await self._client.search(
            index=self._index_name,
            query=text_query,
            size=limit,
        )
        return self._parse_hits(cast(dict[str, Any], result.body))

    async def search_vector_hits(
        self,
        embedding: list[float],
        *,
        allowed_keys: frozenset[KeyT] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[ItemT]]:
        """按资源白名单执行向量检索并解析命中。"""
        if allowed_keys is not None and not allowed_keys:
            return []
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": embedding,
            "k": limit,
            "num_candidates": min(10_000, max(100, limit * 10)),
            "similarity": score_threshold,
        }
        if allowed_keys is not None:
            knn["filter"] = self._resource_filter(allowed_keys)
        result = await self._client.search(
            index=self._index_name,
            knn=knn,
            size=limit,
        )
        return self._parse_hits(cast(dict[str, Any], result.body))

    @staticmethod
    @abstractmethod
    def _resource_filter(allowed_keys: frozenset[KeyT]) -> dict[str, Any]:
        """构造当前资源类型的权限白名单过滤条件。"""

    def _parse_hits(
        self,
        result: dict[str, Any],
    ) -> list[SearchHit[ItemT]]:
        """将 Elasticsearch 命中转换为领域结果。"""
        search_hits = result["hits"]["hits"]
        converted: list[SearchHit[ItemT]] = []
        for hit in search_hits:
            document_id = "<missing>"
            try:
                document_id = str(hit["_id"])
                payload = hit["_source"]["payload"]
                converted.append(
                    SearchHit(
                        item=self._payload_type(**payload),
                        score=float(hit.get("_score") or 0.0),
                    )
                )
            except (TypeError, ValueError, KeyError) as exc:
                logger.bind(
                    index_name=self._index_name,
                    document_id=document_id,
                    stage="read-corrupted-document",
                ).warning("语义索引读取到损坏文档")
                raise CorruptedSemanticIndexDocumentError(
                    resource_label="语义索引",
                    index_name=self._index_name,
                    document_id=document_id,
                ) from exc
        return converted
