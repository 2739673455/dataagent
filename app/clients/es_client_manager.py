"""Elasticsearch 客户端管理"""

from elasticsearch import AsyncElasticsearch

from app.conf.app_config import ESConfig, cfg


class ESClientManager:
    """Elasticsearch 客户端管理器"""

    def __init__(self, es_config: ESConfig) -> None:
        """初始化 Elasticsearch 客户端管理器"""
        self._es_config = es_config
        self._client: AsyncElasticsearch | None = None

    @property
    def _url(self) -> str:
        """获取 Elasticsearch 连接 URL"""
        return f"http://{self._es_config.host}:{self._es_config.port}"

    def init(self) -> None:
        """初始化 Elasticsearch 客户端"""
        self._client = AsyncElasticsearch(hosts=[self._url])

    def get_client(self) -> AsyncElasticsearch:
        """获取 Elasticsearch 客户端"""
        if self._client is None:
            raise RuntimeError("Elasticsearch client manager is not initialized")
        return self._client

    async def close(self) -> None:
        """关闭 Elasticsearch 客户端并释放资源"""
        if self._client is not None:
            await self._client.close()
        self._client = None


es_client_manager = ESClientManager(cfg.elasticsearch)

if __name__ == "__main__":
    import asyncio

    es_client_manager.init()

    async def test() -> None:
        client = es_client_manager.get_client()
        try:
            # 创建索引
            if not await client.indices.exists(index="my-books"):
                await client.indices.create(
                    index="my-books",
                    mappings={
                        "dynamic": False,
                        "properties": {
                            "name": {"type": "text"},
                            "author": {"type": "text"},
                            "release_date": {
                                "type": "date",
                                "format": "yyyy-MM-dd",
                            },
                            "page_count": {"type": "integer"},
                        },
                    },
                )

            # 插入数据
            bulk_response = await client.bulk(
                operations=[
                    {
                        "index": {
                            "_index": "my-books",
                            "_id": "revelation-space",
                        }
                    },
                    {
                        "name": "Revelation Space",
                        "author": "Alastair Reynolds",
                        "release_date": "2000-03-15",
                        "page_count": 585,
                    },
                    {"index": {"_index": "my-books", "_id": "1984"}},
                    {
                        "name": "1984",
                        "author": "George Orwell",
                        "release_date": "1985-06-01",
                        "page_count": 328,
                    },
                    {"index": {"_index": "my-books", "_id": "fahrenheit-451"}},
                    {
                        "name": "Fahrenheit 451",
                        "author": "Ray Bradbury",
                        "release_date": "1953-10-15",
                        "page_count": 227,
                    },
                    {"index": {"_index": "my-books", "_id": "brave-new-world"}},
                    {
                        "name": "Brave New World",
                        "author": "Aldous Huxley",
                        "release_date": "1932-06-01",
                        "page_count": 268,
                    },
                    {"index": {"_index": "my-books", "_id": "handmaids-tale"}},
                    {
                        "name": "The Handmaids Tale",
                        "author": "Margaret Atwood",
                        "release_date": "1985-06-01",
                        "page_count": 311,
                    },
                ],
                refresh="wait_for",
            )
            if bulk_response.get("errors"):
                raise RuntimeError("Elasticsearch bulk indexing contains failed items")

            # 搜索
            resp = await client.search(
                index="my-books",
                query={"match": {"name": "brave"}},
            )
            print(resp)
        finally:
            try:
                await client.indices.delete(
                    index="my-books",
                    ignore_unavailable=True,
                )
            finally:
                await es_client_manager.close()

    asyncio.run(test())
