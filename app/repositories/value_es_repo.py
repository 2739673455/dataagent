"""字段值索引访问"""

import uuid
from dataclasses import asdict
from typing import Any, ClassVar

from elasticsearch import AsyncElasticsearch

from app.conf.app_config import cfg
from app.models.meta import ColumnKey, ValueInfo, column_resource_key
from app.models.semantic_search import SearchHit


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
                properties={"resource_key": {"type": "keyword"}},
            )
        else:
            await self._client.indices.create(
                index=self._index_name, mappings=self._index_mappings
            )

    async def index(self, value_infos: list[ValueInfo], batch_size: int = 500) -> None:
        """批量写入字段取值索引"""
        for i in range(0, len(value_infos), batch_size):
            batch = value_infos[i : i + batch_size]
            operations = []
            for value_info in batch:
                operations.append(
                    {
                        "index": {
                            "_index": self._index_name,
                            "_id": str(
                                uuid.uuid5(
                                    uuid.NAMESPACE_URL,
                                    "value:"
                                    f"{value_info.t_name}:"
                                    f"{value_info.c_name}:"
                                    f"{value_info.value}",
                                )
                            ),
                        }
                    }
                )
                operations.append(
                    {
                        **asdict(value_info),
                        "resource_key": column_resource_key(
                            value_info.t_name,
                            value_info.c_name,
                        ),
                    }
                )
            result = await self._client.bulk(operations=operations, refresh=False)
            if result.get("errors"):
                raise RuntimeError("Elasticsearch bulk indexing contains failed items")

    async def refresh(self) -> None:
        """刷新字段取值索引"""
        await self._client.indices.refresh(index=self._index_name)

    async def delete_by_column(self, t_name: str, c_name: str) -> None:
        """删除字段对应的全部取值"""
        if not await self._client.indices.exists(index=self._index_name):
            return
        await self._client.delete_by_query(
            index=self._index_name,
            query={
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
                                    {"term": {"c_name": c_name}},
                                ]
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
            conflicts="proceed",
            refresh=True,
        )

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
            raise ValueError("allowed_columns must not be empty")
        return {
            "terms": {
                "resource_key": [
                    column_resource_key(t_name, c_name)
                    for t_name, c_name in sorted(allowed_columns)
                ]
            }
        }
