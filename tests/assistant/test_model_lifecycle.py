"""模型客户端在独立事件循环内创建和关闭。"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import httpx
import pytest
from langchain_openai import ChatOpenAI

from app.assistant.agents import manager as agent_manager
from app.assistant.agents import model_factory
from app.shared.config.app_config import LMConfigCfg, ModelCfg, ModelProfileCfg, cfg


@pytest.mark.parametrize("provider", ["openai", "deepseek", "openrouter", "custom"])
def test_clients_are_scoped_to_each_model_context(provider: str) -> None:
    config = LMConfigCfg(
        active="test",
        models={
            "test": ModelCfg.model_validate(
                {
                    "model_provider": provider,
                    "model": "test-model",
                    "base_url": "https://example.invalid/v1",
                    "api_key": "test-key",
                    "params": {"default_headers": {"X-Test": "1"}},
                    "profile": ModelProfileCfg(
                        image_inputs=False,
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
                    model_factory.DataAgentDeepSeek
                    if provider == "deepseek"
                    else ChatOpenAI
                )
                assert type(model) is expected_class
                assert model.use_responses_api is False
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
        MagicMock(), MagicMock(), recall=MagicMock(), query=MagicMock()
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


def test_deepseek_stream_and_tool_continuation_preserve_reasoning() -> None:
    import json

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from app.assistant.messages import reasoning_text

    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        requests.append(json.loads(request.content))
        deltas = (
            [
                {"role": "assistant", "reasoning_content": "先查"},
                {"reasoning_content": "数据"},
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ]
                },
            ]
            if len(requests) == 1
            else [{"role": "assistant", "content": "回答"}]
        )
        chunks = [
            {
                "id": "answer",
                "model": "deepseek-flash",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            for delta in deltas
        ]
        chunks.append(
            {
                "id": "answer",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if len(requests) == 1 else "stop",
                    }
                ],
            }
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
            + "data: [DONE]\n\n",
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = model_factory.DataAgentDeepSeek(
                model="deepseek-flash",
                api_key="test",
                base_url="https://example.invalid/v1",
                http_async_client=client,
                use_responses_api=False,
            )
            chunks = [
                chunk async for chunk in model.astream([HumanMessage(content="查询")])
            ]
            assert (
                "".join(reasoning_text(chunk) or "" for chunk in chunks) == "先查数据"
            )
            combined = chunks[0]
            for chunk in chunks[1:]:
                combined += chunk
            assistant = AIMessage(
                content=combined.content,
                additional_kwargs=combined.additional_kwargs,
                tool_calls=combined.tool_calls,
            )
            messages = [
                HumanMessage(content="查询"),
                assistant,
                ToolMessage(content="结果", tool_call_id="call_1"),
            ]
            answer = [chunk async for chunk in model.astream(messages)]
            assert "".join(chunk.text for chunk in answer) == "回答"
            assert requests[1]["messages"][1]["reasoning_content"] == "先查数据"
            assert requests[1]["messages"][1]["tool_calls"][0]["id"] == "call_1"
            assert requests[1]["messages"][2]["tool_call_id"] == "call_1"
            payload = model._get_request_payload([AIMessage(content="无思考历史")])
            assert payload["messages"][0]["reasoning_content"] == ""
            assert model._get_ls_params()["ls_provider"] == "deepseek"

    asyncio.run(run())
