"""OpenAI 兼容的远程 Embedding 客户端。"""

import httpx

from app.shared.config.app_config import EmbeddingConfig


class EmbeddingClient:
    """持有向量接口的 HTTP 客户端并校验返回结果。"""

    def __init__(self, config: EmbeddingConfig) -> None:
        """创建 HTTP 客户端，按配置设置可选鉴权。"""
        self._config = config
        headers = {}
        if config.api_key is not None and (
            api_key := config.api_key.get_secret_value()
        ):
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout,
            headers=headers,
        )

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """生成向量，按原始文本顺序返回并检查数量。"""
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings",
            json={"model": self._config.model, "input": texts},
        )
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list):
            raise TypeError("Embedding 响应缺失 data 列表")
        embeddings = [
            item["embedding"] for item in sorted(data, key=lambda item: item["index"])
        ]
        if len(embeddings) != len(texts):
            raise ValueError(
                f"Embedding 响应数量不匹配: 期望 {len(texts)} 条，实际返回 {len(embeddings)} 条"
            )
        return embeddings

    async def close(self) -> None:
        """释放 HTTP 连接。"""
        await self._client.aclose()
