"""字段语义索引访问。"""

from typing import Any

from elasticsearch import AsyncElasticsearch
from loguru import logger

from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnKey,
    column_resource_key,
)
from app.metadata.models.search import (
    SemanticIndexDelta,
    SemanticIndexDocument,
)
from app.metadata.repositories.semantic_index import (
    CorruptedSemanticIndexDocumentError,
    SemanticIndexRepo,
    column_resource_terms_filter,
    semantic_index_mappings,
)
from app.shared.config.app_config import cfg
from app.shared.contracts.search import SearchHit


class ColumnESRepo:
    """字段全文与向量索引存储。"""

    _index_name = cfg.elasticsearch.column_index
    _index_mappings = semantic_index_mappings(
        {"t_name": {"type": "keyword"}},
    )

    def __init__(self, client: AsyncElasticsearch) -> None:
        """初始化字段语义索引存储。"""
        self._repo = SemanticIndexRepo(
            client,
            index_name=self._index_name,
            resource_label="字段语义索引",
            mappings=self._index_mappings,
        )

    async def ensure_index(self) -> None:
        """确保字段语义索引存在。"""
        await self._repo.ensure_index()

    async def list_resource_documents(
        self,
        resource_key: str,
    ) -> list[SemanticIndexDocument]:
        """读取字段当前语义索引文档。"""
        result = await self._repo.list_documents(
            {"term": {"resource_key": resource_key}}
        )
        if result.corrupted_document_ids:
            logger.bind(
                index_name=self._index_name,
                document_ids=result.corrupted_document_ids,
                resource_key=resource_key,
                stage="rebuild-corrupted-resource",
            ).warning("字段语义索引检测到损坏文档，开始重建当前资源")
            await self._repo.delete_by_filter([{"term": {"resource_key": resource_key}}])
            return []
        return result.documents

    async def apply_delta(self, delta: SemanticIndexDelta) -> None:
        """应用字段语义索引差量。"""
        await self._repo.apply_delta(delta)

    async def delete(self, t_name: str, c_name: str) -> None:
        """删除字段对应的全部语义索引文档。"""
        await self._repo.delete_by_filter(
            [{"term": {"resource_key": column_resource_key(t_name, c_name)}}]
        )

    async def search_vector_hits(
        self,
        embedding: list[float],
        *,
        allowed_columns: frozenset[ColumnKey] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[ColumnInfo]]:
        """根据向量检索字段并保留命中分数。"""
        result = await self._repo.search_vector(
            embedding,
            score_threshold=score_threshold,
            limit=limit,
            resource_filter=(
                column_resource_terms_filter(allowed_columns)
                if allowed_columns is not None
                else None
            ),
        )
        return self._hits(result)

    async def search_text_hits(
        self,
        query: str,
        *,
        allowed_columns: frozenset[ColumnKey] | None,
        limit: int = 5,
    ) -> list[SearchHit[ColumnInfo]]:
        """根据关键词检索字段并保留命中分数。"""
        result = await self._repo.search_text(
            query,
            limit=limit,
            resource_filter=(
                column_resource_terms_filter(allowed_columns)
                if allowed_columns is not None
                else None
            ),
        )
        return self._hits(result)

    @classmethod
    def _hits(cls, result: dict[str, Any]) -> list[SearchHit[ColumnInfo]]:
        """将 Elasticsearch 命中转换为字段结果。"""
        search_hits = result["hits"]["hits"]
        converted: list[SearchHit[ColumnInfo]] = []
        for hit in search_hits:
            document_id = (
                str(hit.get("_id"))
                if isinstance(hit, dict) and hit.get("_id") is not None
                else "<missing>"
            )
            source_value = hit.get("_source") if isinstance(hit, dict) else None
            resource_key = (
                source_value.get("resource_key")
                if isinstance(source_value, dict)
                and isinstance(source_value.get("resource_key"), str)
                else "<missing>"
            )
            try:
                if not isinstance(hit, dict):
                    raise TypeError("搜索命中不是对象")
                source = hit.get("_source")
                if not isinstance(source, dict):
                    raise TypeError("搜索命中缺少对象类型的 _source")
                payload = source.get("payload")
                if not isinstance(payload, dict):
                    raise TypeError("搜索命中 payload 必须为对象")
                converted.append(
                    SearchHit(
                        item=ColumnInfo(**payload),
                        score=float(hit.get("_score") or 0.0),
                    )
                )
            except (TypeError, ValueError, KeyError) as exc:
                logger.bind(
                    index_name=cls._index_name,
                    document_id=document_id,
                    resource_key=resource_key,
                    stage="read-corrupted-document",
                ).warning("字段语义索引读取到损坏文档")
                raise CorruptedSemanticIndexDocumentError(
                    resource_label="字段语义索引",
                    index_name=cls._index_name,
                    document_id=document_id,
                ) from exc
        return converted
