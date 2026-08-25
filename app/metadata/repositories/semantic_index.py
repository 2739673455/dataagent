"""语义索引差量读写原语"""

from typing import Any, cast

from elasticsearch import AsyncElasticsearch

from app.metadata.search_models import (
    SemanticIndexDelta,
    SemanticIndexDocument,
    SemanticTextType,
)

_SOURCE_FIELDS = [
    "resource_key",
    "text",
    "text_type",
    "embedding_revision",
    "meta_version",
    "payload_hash",
    "payload",
]


class SemanticIndexDeltaRepo:
    """为字段和指标索引提供统一差量操作"""

    def __init__(
        self,
        client: AsyncElasticsearch,
        index_name: str,
        error_resource: str,
    ) -> None:
        """绑定 Elasticsearch 客户端、索引名称和错误资源类型"""
        self._client = client
        self._index_name = index_name
        self._error_resource = error_resource

    async def list_documents(
        self,
        query: dict[str, Any],
    ) -> list[SemanticIndexDocument]:
        """读取一个资源下参与差量比较的全部文档"""
        if not await self._client.indices.exists(index=self._index_name):
            return []
        result = await self._client.search(
            index=self._index_name,
            query=query,
            source={"includes": _SOURCE_FIELDS},
            size=1000,
        )
        body = result.body if hasattr(result, "body") else result
        hits = cast(dict[str, Any], body).get("hits", {}).get("hits", [])
        documents: list[SemanticIndexDocument] = []
        for hit in hits:
            source = hit.get("_source")
            if not isinstance(source, dict):
                continue
            text_value = source.get("text")
            text_type_value = source.get("text_type")
            if not isinstance(text_value, str) or text_type_value not in {
                "name",
                "description",
                "alias",
            }:
                continue
            payload = source.get("payload")
            documents.append(
                SemanticIndexDocument(
                    id=str(hit["_id"]),
                    resource_key=str(source.get("resource_key") or ""),
                    text=text_value,
                    text_type=cast(SemanticTextType, text_type_value),
                    embedding=None,
                    embedding_revision=str(
                        source.get("embedding_revision") or ""
                    ),
                    meta_version=int(source.get("meta_version") or 0),
                    payload_hash=str(source.get("payload_hash") or ""),
                    payload=payload if isinstance(payload, dict) else {},
                )
            )
        return documents

    async def apply_delta(
        self,
        delta: SemanticIndexDelta,
        *,
        batch_size: int = 100,
    ) -> None:
        """混合执行语义文档新增、更新和删除"""
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
            operations: list[dict[str, Any]] = []
            for metadata, source in actions[offset : offset + batch_size]:
                operations.append(metadata)
                if source is not None:
                    operations.append(source)
            result = await self._client.bulk(operations=operations, refresh=False)
            body = result.body if hasattr(result, "body") else result
            payload = cast(dict[str, Any], body)
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
                    f"Elasticsearch {self._error_resource}差量写入失败: "
                    f"{failures[:3]}"
                )
        if actions:
            await self._client.indices.refresh(index=self._index_name)

    @staticmethod
    def _document_source(
        document: SemanticIndexDocument,
        *,
        include_embedding: bool,
    ) -> dict[str, Any]:
        """构造语义索引文档源数据"""
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
