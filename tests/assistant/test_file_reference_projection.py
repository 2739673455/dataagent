"""文件引用在下载与模型消息投影间复用。"""

import json
from datetime import UTC, datetime
from unittest.mock import patch

from deepagents.backends.protocol import FileDownloadResponse
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage

from app.assistant.agents.middleware import message_context as context


def test_images_without_message_ids_reuse_parsing_and_downloads():
    first = ToolMessage(
        name="view_image",
        tool_call_id="first",
        content=json.dumps({"type": "image_view_request", "f_path": "./chart.png"}),
    )
    second = ToolMessage(
        name="view_image",
        tool_call_id="second",
        content=json.dumps({"type": "image_view_request", "f_path": "chart.png"}),
    )
    messages: list[AnyMessage] = [first, second]
    original = [message.model_dump() for message in messages]
    with patch.object(
        context, "_read_image_view_request", wraps=context._read_image_view_request
    ) as parse:
        attachments, images = context._file_references(
            messages, "/data/conversation", "/data/conversation/session"
        )
        paths = context._download_paths(
            attachments, images, load_user_images=False, load_tool_images=True
        )
        assert paths == ["/data/conversation/session/chart.png"]
        projected = context._project_messages(
            messages,
            [
                FileDownloadResponse(
                    path="/data/conversation/session/chart.png", content=b"image"
                )
            ],
            attachment_paths=attachments,
            image_requests=images,
            project_user_images=False,
            project_tool_images=True,
        )
    assert parse.call_count == 2
    for message in projected:
        assert isinstance(message.content, list)
        image = message.content[-1]
        assert isinstance(image, dict)
        assert image["type"] == "image"
        assert image["base64"] == "aW1hZ2U="
    assert [message.model_dump() for message in messages] == original


def test_persisted_attachments_are_checked_again_before_loading():
    message = HumanMessage(
        content="analyze",
        additional_kwargs={
            context.USER_MESSAGE_CONTEXT_KEY: {
                "received_at": datetime.now(UTC).isoformat(),
                "attachments": [
                    {"f_path": "uploads/tmp/../chart.png"},
                    {"f_path": "../other/private.png"},
                    {"f_path": "/skills/private.png"},
                ],
            }
        },
    )
    attachments, images = context._file_references(
        [message], "/data/conversation", "/data/conversation/session"
    )
    paths = context._download_paths(
        attachments, images, load_user_images=True, load_tool_images=True
    )
    assert paths == ["/data/conversation/uploads/chart.png"]
    projected = context._project_messages(
        [message],
        [FileDownloadResponse(path=paths[0], content=b"image")],
        attachment_paths=attachments,
        image_requests=images,
        project_user_images=True,
        project_tool_images=True,
    )
    assert str(projected[0].content).count("invalid_path") == 2
    assert projected[0] is not message
    assert message.content == "analyze"
