"""字段值索引访问"""

import uuid
from dataclasses import asdict
from typing import Any, ClassVar

from elasticsearch import AsyncElasticsearch

from app.conf.app_config import cfg
from app.entities.meta import ValueInfo
from app.entities.semantic_search import SearchHit


class ValueESRepo:
    """字段取值索引存储"""

    _index_name = cfg.elasticsearch.value_index
    _index_mappings: ClassVar[dict[str, Any]] = {
        "dynamic": False,
        "properties": {
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
        if not await self._client.indices.exists(index=self._index_name):
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
                operations.append(asdict(value_info))
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
                    "filter": [
                        {"term": {"t_name": t_name}},
                        {"term": {"c_name": c_name}},
                    ]
                }
            },
            conflicts="proceed",
            refresh=True,
        )

    async def search_hits(
        self,
        keyword: str,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[SearchHit[ValueInfo]]:
        """根据关键词检索字段取值并保留命中分数"""
        query: dict[str, Any] = {"match": {"value": keyword}}
        result = await self._client.search(
            index=self._index_name,
            query=query,
            min_score=score_threshold,
            size=limit,
        )
        return [
            SearchHit(
                item=ValueInfo(**hit["_source"]),
                score=float(hit.get("_score") or 0.0),
            )
            for hit in result["hits"]["hits"]
        ]
