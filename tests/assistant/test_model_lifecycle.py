"""模型客户端在独立事件循环内创建和关闭。"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_openai import ChatOpenAI

from app.assistant import model_factory
from app.assistant.services import manager as agent_manager
from app.shared.config.app_config import LMConfigCfg, ModelCfg, ModelProfileCfg, cfg


@pytest.mark.parametrize(
    ("provider", "protocol"),
    [
        ("openai", "responses"),
        ("deepseek", "responses"),
        ("openai", "chat_completions"),
        ("openrouter", "chat_completions"),
        ("openrouter", "responses"),
        ("deepseek", "chat_completions"),
        ("custom", "chat_completions"),
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
                expected_class = (
                    model_factory.DataAgentDeepSeekResponses
                    if provider == "deepseek" and protocol == "responses"
                    else ChatOpenAI
                )
                assert type(model) is expected_class
                assert model.use_responses_api is (protocol == "responses")
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
        if opened:
            raise RuntimeError("model initialization")
        opened.append(name)
        try:
            yield MagicMock()
        finally:
            closed.append(name)

    factory = agent_manager.AgentManager(
        MagicMock(), MagicMock(), MagicMock(), recall=MagicMock(), query=MagicMock()
    )

    factory._model_names = {"first", "second"}

    async def run() -> None:
        with pytest.raises(RuntimeError, match="model initialization"):
            await factory.init()
        assert opened
        assert closed == list(reversed(opened))
        await factory.close()

    with patch.object(agent_manager, "create_configured_model", model_context):
        asyncio.run(run())


def test_deepseek_async_stream_preserves_reasoning_and_message_id() -> None:
    response = MagicMock()
    response.__aiter__.return_value = [
        SimpleNamespace(
            type="response.reasoning_text.delta",
            output_index=0,
            content_index=0,
            delta="思考",
        ),
        SimpleNamespace(
            type="response.output_text.delta",
            output_index=1,
            content_index=0,
            delta="回答",
        ),
    ]
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=response)
    model = MagicMock(output_version="responses/v1")
    model.root_async_client.responses.create = AsyncMock(return_value=stream)
    callback = AsyncMock()

    async def run() -> None:
        chunks = [
            chunk
            async for chunk in model_factory.DataAgentDeepSeekResponses._astream(
                model,
                [],
                run_manager=callback,
            )
        ]
        assert len(chunks) == 2
        assert chunks[0].message.id
        assert chunks[0].message.id == chunks[1].message.id
        assert chunks[0].message.content == [
            {
                "type": "reasoning",
                "content": [{"type": "reasoning_text", "text": "思考", "index": 0}],
                "index": 0,
            }
        ]
        assert chunks[1].text == "回答"
        assert callback.on_llm_new_token.await_count == 2
        stream.__aexit__.assert_awaited_once()

    asyncio.run(run())
