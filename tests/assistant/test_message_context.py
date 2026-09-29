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
    backend = MagicMock()
    middleware = MessageContextMiddleware(backend)
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
    backend.download_files.assert_not_called()
    backend.adownload_files.assert_not_called()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("supports_images", [False, True])
def test_image_projection_deduplicates_downloads_and_preserves_failed_messages(
    asynchronous, supports_images
):
    import json

    from deepagents.backends.protocol import FileDownloadResponse
    from langchain_core.messages import ToolMessage

    def image_request(path):
        return json.dumps({"type": "image_view_request", "f_path": path})

    messages = [
        ToolMessage(
            content=image_request("/data/chart.png"),
            name="view_image",
            tool_call_id="first",
        ),
        ToolMessage(
            content=image_request("/data/chart.png"),
            name="view_image",
            tool_call_id="second",
        ),
        ToolMessage(
            content=image_request("/data/failed.png"),
            name="view_image",
            tool_call_id="failed",
        ),
        ToolMessage(
            content=image_request("/data/missing.png"),
            name="view_image",
            tool_call_id="missing",
        ),
        ToolMessage(content="not json", name="view_image", tool_call_id="invalid"),
        ToolMessage(content="[]", name="view_image", tool_call_id="array"),
        ToolMessage(
            content='{"status":"error"}', name="view_image", tool_call_id="error"
        ),
        ToolMessage(
            content=image_request("/data/other.png"), name="other", tool_call_id="other"
        ),
    ]
    originals = [message.model_dump() for message in messages]
    reply = AIMessage(content="done")
    model = FakeMessagesListChatModel(
        responses=[reply], profile={"image_tool_message": supports_images}
    )
    request = ModelRequest(model=model, messages=messages)
    responses = [
        FileDownloadResponse(path="/data/chart.png", content=b"image"),
        FileDownloadResponse(path="/data/failed.png", error="file_not_found"),
    ]
    backend = MagicMock(
        download_files=MagicMock(return_value=responses),
        adownload_files=AsyncMock(return_value=responses),
    )
    middleware = MessageContextMiddleware(backend)
    result = ModelResponse(result=[reply])
    handler = (
        AsyncMock(return_value=result)
        if asynchronous
        else MagicMock(return_value=result)
    )
    if asynchronous:
        assert asyncio.run(middleware.awrap_model_call(request, handler)) is result
    else:
        assert middleware.wrap_model_call(request, handler) is result
    projected = handler.call_args.args[0].messages
    if supports_images:
        download = backend.adownload_files if asynchronous else backend.download_files
        download.assert_called_once_with(
            ["/data/chart.png", "/data/failed.png", "/data/missing.png"]
        )
        assert projected[0].content == projected[1].content
        assert projected[0].content == [
            {"type": "image", "base64": "aW1hZ2U=", "mime_type": "image/png"}
        ]
        assert "file_not_found" in projected[2].text
        assert "unavailable" in projected[3].text
        for index in range(4, len(messages)):
            assert projected[index] is messages[index]
    else:
        backend.download_files.assert_not_called()
        backend.adownload_files.assert_not_called()
        assert all(
            before is after for before, after in zip(messages, projected, strict=True)
        )
    assert [message.model_dump() for message in messages] == originals
