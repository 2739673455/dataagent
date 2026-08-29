"""字段值索引访问"""

import json
import uuid
from typing import Any, ClassVar

from elasticsearch import AsyncElasticsearch

from app.metadata.models.catalog import ColumnKey, ValueInfo, column_resource_key
from app.metadata.models.search import SearchHit
from app.shared.config.app_config import cfg


def _value_document_id(value_info: ValueInfo) -> str:
    """生成无歧义且稳定的字段取值文档编号"""
    identity = json.dumps(
        ["value", value_info.t_name, value_info.c_name, value_info.value],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


class ValueESRepo:
    """字段取值索引存储"""

    _index_name = cfg.elasticsearch.value_index
    _index_mappings: ClassVar[dict[str, Any]] = {
        "dynamic": False,
        "properties": {
            "resource_key": {"type": "keyword"},
            "value": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_max_word",
            },
            "t_name": {"type": "keyword"},
            "c_name": {"type": "keyword"},
            "sync_generation": {"type": "keyword"},
        },
    }

    def __init__(self, client: AsyncElasticsearch) -> None:
        """初始化字段取值索引存储"""
        self._client = client

    async def ensure_index(self) -> None:
        """确保字段取值索引存在"""
        if await self._client.indices.exists(index=self._index_name):
            await self._client.indices.put_mapping(
                index=self._index_name,
                properties={
                    "resource_key": {"type": "keyword"},
                    "sync_generation": {"type": "keyword"},
                },
            )
        else:
            await self._client.indices.create(
                index=self._index_name, mappings=self._index_mappings
            )

    async def upsert(
        self,
        value_infos: list[ValueInfo],
        generation: str,
        batch_size: int = 500,
    ) -> None:
        """按稳定编号批量覆盖字段取值索引"""
        for i in range(0, len(value_infos), batch_size):
            batch = value_infos[i : i + batch_size]
            operations = []
            for value_info in batch:
                operations.append(
                    {
                        "index": {
                            "_index": self._index_name,
                            "_id": _value_document_id(value_info),
                        }
                    }
                )
                operations.append(
                    {
                        "value": value_info.value,
                        "t_name": value_info.t_name,
                        "c_name": value_info.c_name,
                        "resource_key": column_resource_key(
                            value_info.t_name,
                            value_info.c_name,
                        ),
                        "sync_generation": generation,
                    }
                )
            result = await self._client.bulk(operations=operations, refresh=False)
            body = result.body if hasattr(result, "body") else result
            if body.get("errors"):
                raise RuntimeError("Elasticsearch 批量写入存在失败项")

    async def refresh(self) -> None:
        """刷新字段取值索引"""
        await self._client.indices.refresh(index=self._index_name)

    async def delete_by_column(self, t_name: str, c_name: str) -> int:
        """删除字段对应的全部取值"""
        if not await self._client.indices.exists(index=self._index_name):
            return 0
        result = await self._client.delete_by_query(
            index=self._index_name,
            query=self._resource_query(t_name, c_name),
            conflicts="proceed",
            refresh=True,
        )
        return self._deleted_count(result)

    async def delete_other_generations(
        self,
        t_name: str,
        c_name: str,
        generation: str,
    ) -> int:
        """删除字段下未进入当前全量同步代次的取值"""
        if not await self._client.indices.exists(index=self._index_name):
            return 0
        result = await self._client.delete_by_query(
            index=self._index_name,
            query={
                "bool": {
                    "filter": [self._resource_query(t_name, c_name)],
                    "must_not": [
                        {"term": {"sync_generation": generation}},
                    ],
                }
            },
            conflicts="proceed",
            refresh=True,
        )
        return self._deleted_count(result)

    async def search_hits(
        self,
        keyword: str,
        *,
        allowed_columns: frozenset[ColumnKey] | None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[ValueInfo]]:
        """根据关键词检索字段取值并保留命中分数"""
        query: dict[str, Any] = {"match": {"value": keyword}}
        if allowed_columns is not None:
            query = {
                "bool": {
                    "must": [query],
                    "filter": [self._column_filter(allowed_columns)],
                }
            }
        result = await self._client.search(
            index=self._index_name,
            query=query,
            min_score=score_threshold,
            size=limit,
        )
        return [
            SearchHit(
                item=ValueInfo(
                    value=hit["_source"]["value"],
                    t_name=hit["_source"]["t_name"],
                    c_name=hit["_source"]["c_name"],
                ),
                score=float(hit.get("_score") or 0.0),
            )
            for hit in result["hits"]["hits"]
        ]

    @staticmethod
    def _column_filter(allowed_columns: frozenset[ColumnKey]) -> dict[str, Any]:
        """构造字段值所属表字段白名单"""
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
    def _resource_query(t_name: str, c_name: str) -> dict[str, Any]:
        """构造字段资源过滤条件"""
        return {"term": {"resource_key": column_resource_key(t_name, c_name)}}

    @staticmethod
    def _deleted_count(result: Any) -> int:
        """校验按查询删除结果并返回删除数量"""
        body = result.body if hasattr(result, "body") else result
        if body.get("failures"):
            raise RuntimeError("Elasticsearch 批量删除取值索引存在失败项")
        return int(body.get("deleted") or 0)
