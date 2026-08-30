"""用户消息模型元数据投影测试"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import ValidationError

from app.analytics.agents.middleware.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
    UserMessageMetadata,
    UserMessageMetadataMiddleware,
    project_user_message_for_model,
)

_RECEIVED_AT = datetime(2026, 8, 29, 2, 15, tzinfo=UTC)


def _private_metadata(received_at: datetime = _RECEIVED_AT) -> dict[str, Any]:
    return UserMessageMetadata(received_at=received_at).model_dump(mode="json")


def _metadata_message(content: Any, *, message_id: str = "user-1") -> HumanMessage:
    return HumanMessage(
        id=message_id,
        content=content,
        additional_kwargs={USER_MESSAGE_METADATA_KEY: _private_metadata()},
    )


def _metadata_blocks(message: BaseMessage) -> list[dict[str, Any]]:
    if not isinstance(message.content, list):
        return []
    return [
        block
        for block in message.content
        if isinstance(block, dict)
        and isinstance(block.get("text"), str)
        and block["text"].startswith("<message_metadata>")
    ]


class UserMessageMetadataModelTest(unittest.TestCase):
    def test_requires_timezone_and_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            UserMessageMetadata(
                received_at=datetime.fromisoformat("2026-08-29T10:15:00")
            )
        with self.assertRaises(ValidationError):
            UserMessageMetadata.model_validate(
                {
                    "received_at": "2026-08-29T02:15:00+00:00",
                    "version": 2,
                }
            )

    def test_normalizes_received_at_to_utc(self) -> None:
        metadata = UserMessageMetadata.model_validate(
            {"received_at": "2026-08-29T10:15:00+08:00"}
        )

        self.assertEqual(metadata.received_at, _RECEIVED_AT)


class UserMessageProjectionTest(unittest.IsolatedAsyncioTestCase):
    def test_projects_string_content_without_mutating_original(self) -> None:
        original = _metadata_message("analyze")

        projected = project_user_message_for_model(original)

        self.assertIsNot(projected, original)
        self.assertEqual(original.content, "analyze")
        self.assertEqual(
            projected.content,
            [
                {
                    "type": "text",
                    "text": (
                        "<message_metadata>"
                        '{"received_at":"2026-08-29T02:15:00Z"}'
                        "</message_metadata>"
                    ),
                },
                {"type": "text", "text": "analyze"},
            ],
        )

    def test_projects_multimodal_content_without_reordering_it(self) -> None:
        content = [
            {"type": "text", "text": "inspect"},
            {"type": "image_url", "image_url": "data:image/png;base64,AA=="},
        ]
        original = _metadata_message(content)

        projected = project_user_message_for_model(original)

        assert isinstance(projected.content, list)
        self.assertEqual(projected.content[1:], content)
        self.assertEqual(original.content, content)
        self.assertEqual(len(_metadata_blocks(projected)), 1)

    def test_leaves_internal_and_malformed_messages_unchanged(self) -> None:
        internal = HumanMessage(content="repair structure")
        malformed = HumanMessage(
            id="malformed",
            content="hello",
            additional_kwargs={
                USER_MESSAGE_METADATA_KEY: {
                    "received_at": "2026-08-29T02:15:00+00:00",
                    "version": 2,
                }
            },
        )

        self.assertIs(project_user_message_for_model(internal), internal)
        self.assertIs(project_user_message_for_model(malformed), malformed)

    async def test_middleware_projects_history_once_on_each_model_call(self) -> None:
        first = _metadata_message("first", message_id="user-1")
        second = _metadata_message(
            [{"type": "text", "text": "second"}],
            message_id="user-2",
        )
        assistant = AIMessage(content="response")
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([])),
            messages=[first, assistant, second],
            tools=[],
        )
        captured: list[ModelRequest[Any]] = []

        async def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(projected)
            return ModelResponse(result=[])

        middleware = UserMessageMetadataMiddleware()
        await middleware.awrap_model_call(request, handler)
        await middleware.awrap_model_call(request, handler)

        self.assertEqual(len(captured), 2)
        for projected_request in captured:
            self.assertEqual(len(_metadata_blocks(projected_request.messages[0])), 1)
            self.assertIs(projected_request.messages[1], assistant)
            self.assertEqual(len(_metadata_blocks(projected_request.messages[2])), 1)
        self.assertEqual(first.content, "first")
        self.assertEqual(second.content, [{"type": "text", "text": "second"}])
