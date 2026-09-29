"""用户消息时间只投影到模型请求，历史时间和原始正文保持不变。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.assistant.agents.middleware.message_context import MessageContextMiddleware


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "content", ["今天的订单", [{"type": "text", "text": "今天的订单"}]]
)
def test_received_time_is_visible_without_mutating_history(asynchronous, content):
    old_time = "2026-09-28T01:00:00+00:00"
    new_time = "2026-09-29T02:00:00+00:00"
    old = HumanMessage(
        content="昨天的订单", additional_kwargs={"received_at": old_time}
    )
    user = HumanMessage(content=content, additional_kwargs={"received_at": new_time})
    internal = HumanMessage(content="内部委派")
    reply = AIMessage(content="done")
    original_messages = [old, user, internal, reply]
    model = FakeMessagesListChatModel(responses=[reply])
    request = ModelRequest(model=model, messages=original_messages)
    middleware = MessageContextMiddleware()
    result = ModelResponse(result=[reply])
    handler = (
        AsyncMock(return_value=result)
        if asynchronous
        else MagicMock(return_value=result)
    )
    for _ in range(2):
        if asynchronous:
            assert asyncio.run(middleware.awrap_model_call(request, handler)) is result
        else:
            assert middleware.wrap_model_call(request, handler) is result
        projected = handler.call_args.args[0].messages
        assert old_time in projected[0].text
        assert new_time in projected[1].text
        assert projected[1].text.count(new_time) == 1
        assert "今天的订单" in projected[1].text
        assert projected[2] is internal
        assert projected[3] is reply
        assert user.content == content
        assert old.content == "昨天的订单"
