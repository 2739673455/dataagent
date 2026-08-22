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
            raise RuntimeError("Elasticsearch 客户端管理器尚未初始化")
        return self._client

    async def close(self) -> None:
        """关闭 Elasticsearch 客户端并释放资源"""
        if self._client is not None:
            await self._client.close()
        self._client = None


es_client_manager = ESClientManager(cfg.elasticsearch)
