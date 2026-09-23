"""Embedding 客户端管理。"""

from __future__ import annotations

from typing import Any

import httpx

from app.shared.config.app_config import EmbeddingConfig


class RemoteEmbeddingClient:
    """OpenAI 兼容的远程 Embedding 客户端。"""

    def __init__(self, config: EmbeddingConfig) -> None:
        """初始化远程 Embedding 客户端。"""
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout,
            headers=self._build_headers(
                config.api_key.get_secret_value()
                if config.api_key is not None
                else None
            ),
        )

    @staticmethod
    def _build_headers(api_key: str | None) -> dict[str, str]:
        """构建 HTTP 请求头。"""
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

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
        return self._parse_embeddings(response.json(), expected_count=len(texts))

    async def aclose(self) -> None:
        """关闭远程 Embedding 客户端。"""
        await self._client.aclose()

    @staticmethod
    def _parse_embeddings(
        payload: dict[str, Any], expected_count: int
    ) -> list[list[float]]:
        """解析 Embedding 响应数据。"""
        data = payload.get("data")
        if not isinstance(data, list):
            raise TypeError("Embedding 响应缺失 data 列表")

        embeddings: list[list[float]] = [
            item["embedding"] for item in sorted(data, key=lambda item: item["index"])
        ]

        if len(embeddings) != expected_count:
            raise ValueError(
                f"Embedding 响应数量不匹配: 期望 {expected_count} 条，实际返回 {len(embeddings)} 条"
            )
        return embeddings


class EmbeddingClientManager:
    """Embedding 客户端管理器。"""

    def __init__(self, config: EmbeddingConfig) -> None:
        """初始化 Embedding 客户端管理器。"""
        self._config = config
        self._client: RemoteEmbeddingClient | None = None

    def init(self) -> None:
        """初始化 Embedding 客户端。"""
        self._client = RemoteEmbeddingClient(self._config)

    def get_client(self) -> RemoteEmbeddingClient:
        """获取 Embedding 客户端。"""
        if self._client is None:
            raise RuntimeError("Embedding 客户端管理器尚未初始化")
        return self._client

    async def close(self) -> None:
        """关闭 Embedding 客户端并释放资源。"""
        resource = self._client
        self._client = None
        if resource is not None:
            await resource.aclose()
