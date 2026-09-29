"""OpenAI 兼容的远程 Embedding 客户端。"""

import httpx

from app.shared.config.app_config import EmbeddingConfig


class EmbeddingClient:
    """OpenAI 兼容的远程 Embedding 客户端。"""

    def __init__(self, config: EmbeddingConfig) -> None:
        """初始化远程 Embedding 客户端。"""
        self._config = config
        headers = {}
        if config.api_key is not None:
            headers["Authorization"] = f"Bearer {config.api_key.get_secret_value()}"
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout,
            headers=headers,
        )

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """生成多个文本的向量。"""
        if not texts:
            return []
        payload = {
            "model": self._config.model,
            "input": texts,
        }
        response = await self._client.post("/embeddings", json=payload)
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list):
            raise TypeError("Embedding 响应缺失 data 列表")

        embeddings: list[list[float]] = [
            item["embedding"] for item in sorted(data, key=lambda item: item["index"])
        ]

        if len(embeddings) != len(texts):
            raise ValueError(
                f"Embedding 响应数量不匹配: 期望 {len(texts)} 条，实际返回 {len(embeddings)} 条"
            )
        return embeddings

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.aclose()
