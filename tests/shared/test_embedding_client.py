"""向量接口请求、顺序恢复及错误响应处理。"""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.config.app_config import EmbeddingConfig


@pytest.mark.parametrize(
    "payload,error",
    [
        (
            {
                "data": [
                    {"index": 1, "embedding": [2.0]},
                    {"index": 0, "embedding": [1.0]},
                ]
            },
            None,
        ),
        ({"data": []}, ValueError),
        ({}, TypeError),
    ],
)
def test_embedding_request_preserves_order_and_checks_count(payload, error):
    config = EmbeddingConfig(
        base_url="https://example.invalid/v1",
        api_key="secret",
        model="embedding",
        timeout=10,
    )

    def respond(request):
        assert request.url.path == "/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer secret"
        assert json.loads(request.content) == {
            "model": "embedding",
            "input": ["a", "b"],
        }
        return httpx.Response(200, json=payload)

    async def run():
        http = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Authorization": "Bearer secret"},
            transport=httpx.MockTransport(respond),
        )
        with patch(
            "app.shared.clients.embedding_client_manager.httpx.AsyncClient",
            return_value=http,
        ):
            client = EmbeddingClient(config)
        try:
            assert await client.aembed_documents([]) == []
            if error:
                with pytest.raises(error):
                    await client.aembed_documents(["a", "b"])
            else:
                assert await client.aembed_documents(["a", "b"]) == [[1.0], [2.0]]
        finally:
            await client.close()
        assert http.is_closed

    asyncio.run(run())
