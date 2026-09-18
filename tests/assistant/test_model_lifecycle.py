"""模型客户端在独立事件循环内创建和关闭。"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter

from app.assistant import model_factory
from app.assistant.execution import runtime_factory
from app.shared.config.app_config import LMConfigCfg, ModelCfg, ModelProfileCfg, cfg


@pytest.mark.parametrize(
    ("provider", "protocol"),
    [
        ("openai", "responses"),
        ("deepseek", "responses"),
        ("openai", "chat_completions"),
        ("openrouter", "chat_completions"),
    ],
)
def test_clients_are_scoped_to_each_model_context(provider: str, protocol: str) -> None:
    config = LMConfigCfg(
        active="test",
        models={
            "test": ModelCfg.model_validate(
                {
                    "model_provider": provider,
                    "api_protocol": protocol,
                    "model": "test-model",
                    "base_url": "https://example.invalid/v1",
                    "api_key": "test-key",
                    "params": {"default_headers": {"X-Test": "1"}},
                    "profile": ModelProfileCfg(
                        image_inputs=False,
                        structured_output=False,
                        max_input_tokens=1024,
                    ),
                }
            )
        },
    )
    clients = []

    async def run() -> None:
        with pytest.raises(RuntimeError, match="operation"):
            async with model_factory.create_configured_model("test") as model:
                if provider == "openrouter":
                    assert isinstance(model, ChatOpenRouter)
                    sdk = model.client.sdk_configuration
                    sync_client, async_client = sdk.client, sdk.async_client
                else:
                    assert isinstance(model, ChatOpenAI)
                    sync_client = model.http_client
                    async_client = model.http_async_client
                assert isinstance(sync_client, httpx.Client)
                assert isinstance(async_client, httpx.AsyncClient)
                assert not sync_client.is_closed
                assert not async_client.is_closed
                clients.append((sync_client, async_client))
                raise RuntimeError("operation")
        assert clients[-1][0].is_closed
        assert clients[-1][1].is_closed

    with patch.object(cfg, "lm_config", config):
        asyncio.run(run())
        asyncio.run(run())
    assert clients[0][0] is not clients[1][0]
    assert clients[0][1] is not clients[1][1]


def test_runtime_init_failure_closes_already_created_models() -> None:
    opened, closed = [], []

    @asynccontextmanager
    async def model_context(name: str) -> AsyncGenerator[MagicMock]:
        opened.append(name)
        try:
            yield MagicMock()
        finally:
            closed.append(name)

    factory = runtime_factory.ConversationAgentRuntimeFactory(
        MagicMock(), MagicMock(), MagicMock(), recall=MagicMock(), query=MagicMock()
    )

    async def run() -> None:
        with pytest.raises(RuntimeError, match="mcp"):
            await factory.init()
        assert opened
        assert closed == list(reversed(opened))
        await factory.close()

    with (
        patch.object(runtime_factory, "create_configured_model", model_context),
        patch.object(
            runtime_factory, "get_mcp_tools", AsyncMock(side_effect=RuntimeError("mcp"))
        ),
    ):
        asyncio.run(run())
