"""语义索引差量读写原语。"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from elasticsearch import AsyncElasticsearch

from app.metadata.models.catalog import ColumnKey, column_resource_key
from app.metadata.models.search import (
    SemanticIndexDelta,
    SemanticIndexDocument,
    SemanticTextType,
)
from app.shared.config.app_config import cfg

_SOURCE_FIELDS = [
    "resource_key",
    "text",
    "text_type",
    "embedding_revision",
    "meta_version",
    "payload_hash",
    "payload",
]
_EXACT_TEXT_BOOSTS: dict[SemanticTextType, float] = {
    "name": 8.0,
    "alias": 6.0,
    "description": 4.0,
}
_UPDATABLE_MAPPING_PROPERTIES: dict[str, Any] = {
    "resource_key": {"type": "keyword"},
    "meta_version": {"type": "long"},
    "embedding_revision": {"type": "keyword"},
    "payload_hash": {"type": "keyword"},
}


@dataclass(frozen=True, slots=True)
class SemanticIndexDocumentReadResult:
    """语义索引资源文档读取结果及损坏文档编号。"""

    documents: list[SemanticIndexDocument]
    corrupted_document_ids: list[str]


class CorruptedSemanticIndexDocumentError(RuntimeError):
    """在线检索读取到无法反序列化的 Elasticsearch 文档。"""

    def __init__(
        self,
        *,
        resource_label: str,
        index_name: str,
        document_id: str,
    ) -> None:
        """保存可用于日志和召回失败记录的定位信息。"""
        self.index_name = index_name
        self.document_id = document_id
        super().__init__(
            f"{resource_label}文档损坏: index={index_name}, document_id={document_id}"
        )


def column_resource_terms_filter(
    allowed_columns: frozenset[ColumnKey],
) -> dict[str, Any]:
    """构造字段资源键白名单 Elasticsearch filter。"""
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


def semantic_index_mappings(
    extra_properties: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造字段和指标共用的语义索引 mapping。"""
    properties: dict[str, Any] = {
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
    }
    if extra_properties is not None:
        properties.update(extra_properties)
    return {"dynamic": False, "properties": properties}


class SemanticIndexRepo:
    """字段和指标索引共用的 Elasticsearch 技术实现。"""

    def __init__(
        self,
        client: AsyncElasticsearch,
        *,
        index_name: str,
        resource_label: str,
        mappings: dict[str, Any],
    ) -> None:
        """绑定 Elasticsearch 客户端、索引定义和业务标签。"""
        self._client = client
        self._index_name = index_name
        self._resource_label = resource_label
        self._mappings = mappings

    async def ensure_index(self) -> None:
        """确保语义索引存在并补充可安全升级的字段。"""
        if await self._client.indices.exists(index=self._index_name):
            await self._client.indices.put_mapping(
                index=self._index_name,
                properties=_UPDATABLE_MAPPING_PROPERTIES,
            )
            return
        await self._client.indices.create(
            index=self._index_name,
            mappings=self._mappings,
        )

    async def list_documents(
        self,
        query: dict[str, Any],
    ) -> SemanticIndexDocumentReadResult:
        """读取一个资源下参与差量比较的全部文档及损坏状态。"""
        if not await self._client.indices.exists(index=self._index_name):
            return SemanticIndexDocumentReadResult([], [])
        result = await self._client.search(
            index=self._index_name,
            query=query,
            source={"includes": _SOURCE_FIELDS},
            size=1000,
        )
        hits = cast(dict[str, Any], result.body).get("hits", {}).get("hits", [])
        documents: list[SemanticIndexDocument] = []
        corrupted_document_ids: list[str] = []
        for hit in hits:
            document_id = (
                str(hit.get("_id"))
                if isinstance(hit, dict) and hit.get("_id") is not None
                else "<missing>"
            )
            try:
                if not isinstance(hit, dict):
                    raise TypeError("搜索命中不是对象")
                if not isinstance(hit.get("_id"), str) or not hit["_id"]:
                    raise ValueError("搜索命中缺少 _id")
                source = hit.get("_source")
                if not isinstance(source, dict):
                    raise TypeError("搜索命中缺少对象类型的 _source")
                text_value = source.get("text")
                if not isinstance(text_value, str):
                    raise TypeError("语义文档 text 必须为字符串")
                text_type_value = source.get("text_type")
                if text_type_value not in {"name", "description", "alias"}:
                    raise ValueError("语义文档 text_type 无效")
                payload = source.get("payload")
                if not isinstance(payload, dict):
                    raise TypeError("语义文档 payload 必须为对象")
                documents.append(
                    SemanticIndexDocument(
                        id=hit["_id"],
                        resource_key=str(source.get("resource_key") or ""),
                        text=text_value,
                        text_type=cast(SemanticTextType, text_type_value),
                        embedding=None,
                        embedding_revision=str(source.get("embedding_revision") or ""),
                        meta_version=int(source.get("meta_version") or 0),
                        payload_hash=str(source.get("payload_hash") or ""),
                        payload=payload,
                    )
                )
            except (TypeError, ValueError, KeyError):
                corrupted_document_ids.append(document_id)
        return SemanticIndexDocumentReadResult(documents, corrupted_document_ids)

    async def apply_delta(
        self,
        delta: SemanticIndexDelta,
        *,
        batch_size: int = 100,
    ) -> None:
        """混合执行语义文档新增、更新和删除。"""
        actions: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for document in delta.create:
            if document.embedding is None:
                raise ValueError("新增语义索引文档缺少向量")
            actions.append(
                (
                    {"index": {"_index": self._index_name, "_id": document.id}},
                    self._document_source(document, include_embedding=True),
                )
            )
        for document in delta.update:
            if document.embedding is None:
                # 文本未变化时使用 partial update，保留 Elasticsearch 中已有向量。
                actions.append(
                    (
                        {"update": {"_index": self._index_name, "_id": document.id}},
                        {
                            "doc": self._document_source(
                                document,
                                include_embedding=False,
                            )
                        },
                    )
                )
            else:
                # 文本或 embedding revision 变化时整条覆盖，确保向量与正文同版本。
                actions.append(
                    (
                        {"index": {"_index": self._index_name, "_id": document.id}},
                        self._document_source(document, include_embedding=True),
                    )
                )
        actions.extend(
            (
                {"delete": {"_index": self._index_name, "_id": document_id}},
                None,
            )
            for document_id in delta.delete_ids
        )
        for offset in range(0, len(actions), batch_size):
            # Bulk API 的 metadata 与 source 分别占一行，batch_size 按业务文档计数。
            operations: list[dict[str, Any]] = []
            for metadata, source in actions[offset : offset + batch_size]:
                operations.append(metadata)
                if source is not None:
                    operations.append(source)
            result = await self._client.bulk(operations=operations, refresh=False)
            payload = cast(dict[str, Any], result.body)
            if payload.get("errors"):
                failures = [
                    item
                    for item in payload.get("items", [])
                    if any(
                        isinstance(value, dict) and value.get("error")
                        for value in item.values()
                    )
                ]
                raise RuntimeError(
                    f"Elasticsearch {self._resource_label}差量写入失败: {failures[:3]}"
                )
        if actions:
            await self._client.indices.refresh(index=self._index_name)

    async def delete_by_filter(self, filters: list[dict[str, Any]]) -> None:
        """按过滤条件删除语义索引文档。"""
        if not await self._client.indices.exists(index=self._index_name):
            return
        await self._client.delete_by_query(
            index=self._index_name,
            query={"bool": {"filter": filters}},
            conflicts="proceed",
            refresh=True,
        )

    async def search_vector(
        self,
        embedding: list[float],
        *,
        score_threshold: float,
        limit: int,
        resource_filter: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """执行语义索引向量检索。"""
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": embedding,
            "k": limit,
            "num_candidates": min(10_000, max(100, limit * 10)),
            "similarity": score_threshold,
        }
        if resource_filter is not None:
            knn["filter"] = resource_filter
        result = await self._client.search(
            index=self._index_name,
            knn=knn,
            size=limit,
        )
        return cast(dict[str, Any], result.body)

    async def search_text(
        self,
        query: str,
        *,
        limit: int,
        resource_filter: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """执行语义索引全文检索。"""
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
        if resource_filter is not None:
            text_query = {
                "bool": {
                    "must": [text_query],
                    "filter": [resource_filter],
                }
            }
        result = await self._client.search(
            index=self._index_name,
            query=text_query,
            size=limit,
        )
        return cast(dict[str, Any], result.body)

    @staticmethod
    def _document_source(
        document: SemanticIndexDocument,
        *,
        include_embedding: bool,
    ) -> dict[str, Any]:
        """构造语义索引文档源数据。"""
        source = {
            "resource_key": document.resource_key,
            "text": document.text,
            "text_type": document.text_type,
            "embedding_revision": document.embedding_revision,
            "meta_version": document.meta_version,
            "payload_hash": document.payload_hash,
            "payload": document.payload,
        }
        if include_embedding:
            source["embedding"] = document.embedding
        return source
