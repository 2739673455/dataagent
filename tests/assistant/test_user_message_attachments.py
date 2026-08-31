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
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.assistant.agents.middleware.user_message_attachments import (
    IMAGE_VIEW_TOOL_NAME,
    USER_MESSAGE_ATTACHMENTS_KEY,
    ImageViewRequest,
    UserMessageAttachment,
    UserMessageAttachmentMiddleware,
    UserMessageAttachments,
)
from app.assistant.agents.middleware.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
    UserMessageMetadata,
)
from app.assistant.agents.tools import create_image_view_request_tool


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


class UserMessageAttachmentProjectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_latest_user_message_images_are_loaded(self) -> None:
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
        backend = _FakeBackend({"uploads/current.png": b"current-image"})
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([])),
            messages=[historical, AIMessage(content="response"), current],
            tools=[],
        )
        captured: list[ModelRequest[Any]] = []

        async def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(projected)
            return ModelResponse(result=[])

        middleware = UserMessageAttachmentMiddleware(cast(BackendProtocol, backend))
        await middleware.awrap_model_call(request, handler)

        self.assertEqual(backend.downloaded, [["uploads/current.png"]])
        projected_historical = cast(HumanMessage, captured[0].messages[0])
        projected_current = cast(HumanMessage, captured[0].messages[2])
        self.assertEqual(len(_blocks(projected_historical, "image_url")), 0)
        self.assertEqual(len(_blocks(projected_current, "image_url")), 1)
        historical_context = _blocks(projected_historical, "text")[-1]["text"]
        self.assertIn(
            '"path":"/uploads/report.csv","tool":"read_file"',
            historical_context,
        )
        self.assertIn(
            '"path":"/uploads/old.png","tool":"view_image"',
            historical_context,
        )
        self.assertEqual(historical.content, "first")
        self.assertEqual(current.content, "second")

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
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([])),
            messages=[user, tool_message],
            tools=[],
        )
        captured: list[ModelRequest[Any]] = []

        async def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(projected)
            return ModelResponse(result=[])

        middleware = UserMessageAttachmentMiddleware(cast(BackendProtocol, backend))
        await middleware.awrap_model_call(request, handler)

        projected_tool = cast(ToolMessage, captured[0].messages[1])
        self.assertEqual(len(_blocks(projected_tool, "image_url")), 1)
        self.assertEqual(tool_message.content, stored_result)

    async def test_missing_current_image_projects_error_without_mutation(self) -> None:
        user = _user_message(
            "inspect",
            message_id="user-1",
            attachments=["uploads/missing.png"],
        )
        backend = _FakeBackend({})
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([])),
            messages=[user],
            tools=[],
        )
        captured: list[ModelRequest[Any]] = []

        async def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(projected)
            return ModelResponse(result=[])

        middleware = UserMessageAttachmentMiddleware(cast(BackendProtocol, backend))
        await middleware.awrap_model_call(request, handler)

        projected = cast(HumanMessage, captured[0].messages[0])
        error_text = _blocks(projected, "text")[-1]["text"]
        self.assertIn("<attachment_error>", error_text)
        self.assertIn('"error":"file_not_found"', error_text)
        self.assertEqual(user.content, "inspect")


class ViewImageToolTest(unittest.TestCase):
    def test_returns_explicit_image_view_request_without_image_bytes(self) -> None:
        result = create_image_view_request_tool().invoke(
            {"f_path": "uploads/chart.png"}
        )

        self.assertEqual(
            result,
            {"type": "image_view_request", "f_path": "uploads/chart.png"},
        )

    def test_rejects_non_image_path(self) -> None:
        result = create_image_view_request_tool().invoke(
            {"f_path": "uploads/report.csv"}
        )

        self.assertEqual(result["code"], "unsupported_image_type")
