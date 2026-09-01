"""用户消息附件模型投影测试。"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from typing import Any, cast

from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
)
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from app.assistant.agents.middleware.user_message_attachments import (
    USER_MESSAGE_ATTACHMENTS_KEY,
    UserMessageAttachment,
    UserMessageAttachmentMiddleware,
    UserMessageAttachments,
)
from app.assistant.agents.middleware.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
    UserMessageMetadata,
)
from app.assistant.agents.tools import create_view_image_tools
from app.assistant.agents.tools.view_image import (
    IMAGE_VIEW_TOOL_NAME,
    ImageViewRequest,
)


def _user_message(
    content: str,
    *,
    message_id: str,
    attachments: list[str] | None = None,
) -> HumanMessage:
    additional_kwargs: dict[str, Any] = {
        USER_MESSAGE_METADATA_KEY: UserMessageMetadata(
            received_at=datetime(2026, 8, 30, tzinfo=UTC)
        ).model_dump(mode="json")
    }
    if attachments:
        additional_kwargs[USER_MESSAGE_ATTACHMENTS_KEY] = UserMessageAttachments(
            attachments=[UserMessageAttachment(f_path=path) for path in attachments]
        ).model_dump(mode="json")
    return HumanMessage(
        id=message_id,
        content=content,
        additional_kwargs=additional_kwargs,
    )


def _blocks(
    message: HumanMessage | ToolMessage, block_type: str
) -> list[dict[str, Any]]:
    if not isinstance(message.content, list):
        return []
    return [
        item
        for item in message.content
        if isinstance(item, dict) and item.get("type") == block_type
    ]


def _responses_image_model() -> GenericFakeChatModel:
    return GenericFakeChatModel(
        messages=iter([]),
        profile={"image_inputs": True, "image_tool_message": True},
    )


def _chat_image_model() -> GenericFakeChatModel:
    return GenericFakeChatModel(
        messages=iter([]),
        profile={"image_inputs": True},
    )


class _FakeBackend:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.downloaded: list[list[str]] = []

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self.downloaded.append(paths)
        return self._responses(paths)

    async def adownload_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        self.downloaded.append(paths)
        return self._responses(paths)

    def _responses(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(
                path=path,
                content=self.files.get(path),
                error=None if path in self.files else "file_not_found",
            )
            for path in paths
        ]


async def _project(
    model: BaseChatModel,
    messages: list[AnyMessage],
    backend: _FakeBackend,
) -> list[AnyMessage]:
    request = ModelRequest(model=model, messages=messages, tools=[])
    captured: list[ModelRequest[Any]] = []

    async def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
        captured.append(projected)
        return ModelResponse(result=[])

    middleware = UserMessageAttachmentMiddleware(cast(BackendProtocol, backend))
    await middleware.awrap_model_call(request, handler)
    return captured[0].messages


class UserMessageAttachmentProjectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_responses_loads_all_retained_user_images(self) -> None:
        historical = _user_message(
            "first",
            message_id="user-1",
            attachments=["uploads/report.csv", "uploads/old.png"],
        )
        current = _user_message(
            "second",
            message_id="user-2",
            attachments=["uploads/current.png"],
        )
        backend = _FakeBackend(
            {
                "uploads/old.png": b"old-image",
                "uploads/current.png": b"current-image",
            }
        )
        projected = await _project(
            _responses_image_model(),
            [historical, AIMessage(content="response"), current],
            backend,
        )

        self.assertEqual(
            backend.downloaded,
            [["uploads/old.png", "uploads/current.png"]],
        )
        projected_historical = cast(HumanMessage, projected[0])
        projected_current = cast(HumanMessage, projected[2])
        self.assertEqual(
            _blocks(projected_historical, "image"),
            [
                {
                    "type": "image",
                    "base64": "b2xkLWltYWdl",
                    "mime_type": "image/png",
                }
            ],
        )
        self.assertEqual(len(_blocks(projected_current, "image")), 1)
        historical_context = _blocks(projected_historical, "text")[-1]["text"]
        self.assertIn(
            '"path":"/uploads/report.csv","tool":"read_file"',
            historical_context,
        )
        self.assertIn(
            '"path":"/uploads/old.png"',
            historical_context,
        )
        self.assertNotIn('"tool":"view_image"', historical_context)
        self.assertEqual(historical.content, "first")
        self.assertEqual(current.content, "second")

    async def test_chat_completions_loads_all_retained_user_images(self) -> None:
        historical = _user_message(
            "first",
            message_id="user-1",
            attachments=["uploads/old.png"],
        )
        current = _user_message(
            "second",
            message_id="user-2",
            attachments=["uploads/current.png"],
        )
        backend = _FakeBackend(
            {
                "uploads/old.png": b"old-image",
                "uploads/current.png": b"current-image",
            }
        )
        projected = await _project(
            _chat_image_model(),
            [historical, AIMessage(content="response"), current],
            backend,
        )

        self.assertEqual(
            backend.downloaded,
            [["uploads/old.png", "uploads/current.png"]],
        )
        projected_historical = cast(HumanMessage, projected[0])
        projected_current = cast(HumanMessage, projected[2])
        self.assertEqual(len(_blocks(projected_historical, "image")), 1)
        self.assertEqual(len(_blocks(projected_current, "image")), 1)
        historical_context = _blocks(projected_historical, "text")[-1]["text"]
        self.assertNotIn('"tool":"view_image"', historical_context)
        self.assertNotIn("图片识别功能未开启", historical_context)
        self.assertEqual(historical.content, "first")
        self.assertEqual(current.content, "second")

    async def test_responses_reloads_previous_image_on_text_follow_up(
        self,
    ) -> None:
        historical = _user_message(
            "first",
            message_id="user-1",
            attachments=["uploads/old.png"],
        )
        current = _user_message("continue", message_id="user-2")
        backend = _FakeBackend({"uploads/old.png": b"old-image"})
        projected = await _project(
            _responses_image_model(),
            [historical, AIMessage(content="response"), current],
            backend,
        )

        self.assertEqual(backend.downloaded, [["uploads/old.png"]])
        projected_historical = cast(HumanMessage, projected[0])
        self.assertEqual(len(_blocks(projected_historical, "image")), 1)

    async def test_current_turn_view_image_result_is_temporarily_expanded(
        self,
    ) -> None:
        user = _user_message("inspect the old image", message_id="user-2")
        stored_result = json.dumps(
            ImageViewRequest(f_path="uploads/old.png").model_dump(mode="json"),
            ensure_ascii=False,
        )
        tool_message = ToolMessage(
            content=stored_result,
            tool_call_id="call-1",
            name=IMAGE_VIEW_TOOL_NAME,
        )
        backend = _FakeBackend({"uploads/old.png": b"old-image"})
        projected = await _project(
            _responses_image_model(),
            [user, tool_message],
            backend,
        )

        projected_tool = cast(ToolMessage, projected[1])
        self.assertEqual(len(_blocks(projected_tool, "image")), 1)
        self.assertEqual(tool_message.content, stored_result)

    async def test_missing_view_image_uses_plain_tool_error(self) -> None:
        user = _user_message("inspect", message_id="user-1")
        stored_result = json.dumps(
            ImageViewRequest(f_path="uploads/missing.png").model_dump(mode="json")
        )
        tool_message = ToolMessage(
            content=stored_result,
            tool_call_id="call-1",
            name=IMAGE_VIEW_TOOL_NAME,
        )
        projected = await _project(
            _responses_image_model(),
            [user, tool_message],
            _FakeBackend({}),
        )

        projected_tool = cast(ToolMessage, projected[1])
        error = json.loads(_blocks(projected_tool, "text")[-1]["text"])
        self.assertEqual(
            error,
            {
                "status": "error",
                "path": "uploads/missing.png",
                "error": "file_not_found",
            },
        )

    async def test_missing_current_image_projects_error_without_mutation(self) -> None:
        user = _user_message(
            "inspect",
            message_id="user-1",
            attachments=["uploads/missing.png"],
        )
        backend = _FakeBackend({})
        projected_messages = await _project(_responses_image_model(), [user], backend)

        projected = cast(HumanMessage, projected_messages[0])
        error_text = _blocks(projected, "text")[-1]["text"]
        self.assertIn("<attachment_error>", error_text)
        self.assertIn('"error":"file_not_found"', error_text)
        self.assertEqual(user.content, "inspect")

    async def test_model_without_image_capabilities_only_receives_paths(self) -> None:
        user = _user_message(
            "inspect",
            message_id="user-1",
            attachments=["uploads/chart.png"],
        )
        backend = _FakeBackend({"uploads/chart.png": b"image"})
        projected_messages = await _project(
            GenericFakeChatModel(messages=iter([])), [user], backend
        )

        self.assertEqual(backend.downloaded, [])
        projected = cast(HumanMessage, projected_messages[0])
        self.assertEqual(_blocks(projected, "image"), [])
        context = _blocks(projected, "text")[-1]["text"]
        self.assertIn('"path":"/uploads/chart.png"', context)
        self.assertIn("当前模型的图片识别功能未开启", context)
        self.assertIn("图片不会被自动加载", context)
        self.assertIn("请勿根据文件名推测图片内容", context)
        self.assertNotIn('"available":false', context)
        self.assertNotIn('"tool":"view_image"', context)


class ViewImageToolTest(unittest.TestCase):
    def test_tool_availability_follows_image_tool_message_profile(self) -> None:
        unsupported = GenericFakeChatModel(messages=iter([]))
        chat_vision = GenericFakeChatModel(
            messages=iter([]),
            profile={"image_inputs": True},
        )
        supported = _responses_image_model()

        self.assertEqual(create_view_image_tools(unsupported), ())
        self.assertEqual(create_view_image_tools(chat_vision), ())
        self.assertEqual(
            create_view_image_tools(supported)[0].name,
            IMAGE_VIEW_TOOL_NAME,
        )

    def test_returns_explicit_image_view_request_without_image_bytes(self) -> None:
        tool = create_view_image_tools(_responses_image_model())[0]
        result = tool.invoke({"f_path": "uploads/chart.png"})

        self.assertEqual(
            result,
            {"type": "image_view_request", "f_path": "uploads/chart.png"},
        )

    def test_rejects_non_image_path(self) -> None:
        tool = create_view_image_tools(_responses_image_model())[0]
        result = tool.invoke({"f_path": "uploads/report.csv"})

        self.assertEqual(result["code"], "unsupported_image_type")
