"""会话标题初始化与后台生成测试"""

from __future__ import annotations

import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from langchain_core.messages import AIMessage

from app.analytics.api.chat import schemas as chat_schema
from app.analytics.api.chat.router import (
    api_create_conversation,
    api_stream_chat,
)
from app.analytics.models import ConversationInfo
from app.analytics.services.conversation_title import (
    ConversationTitleService,
    initial_conversation_title,
    normalize_generated_title,
)

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


@asynccontextmanager
async def _unlocked(*_: object) -> AsyncGenerator[None]:
    yield


def _conversation(
    title: str,
    *,
    title_pending: bool,
    is_draft: bool = False,
) -> ConversationInfo:
    now = datetime.now(UTC)
    return ConversationInfo(
        id=_CONVERSATION_ID,
        user_id=7,
        title=title,
        title_pending=title_pending,
        is_draft=is_draft,
        create_at=now,
        update_at=now,
    )


class ConversationTitleTest(unittest.IsolatedAsyncioTestCase):
    def test_initial_title_uses_first_64_trimmed_characters(self) -> None:
        source = "  " + "数" * 70 + "  "
        self.assertEqual(initial_conversation_title(source), "数" * 64)
        self.assertEqual(initial_conversation_title(" \n "), "新对话")

    def test_generated_title_is_normalized_and_limited(self) -> None:
        self.assertEqual(
            normalize_generated_title("  标题： “华东销售趋势分析”  "),
            "华东销售趋势分析",
        )
        self.assertEqual(len(normalize_generated_title("题" * 80)), 64)

    async def test_model_title_updates_unchanged_initial_title(self) -> None:
        repository = MagicMock()
        repository.get = AsyncMock(
            return_value=_conversation("查看华东销售趋势", title_pending=False)
        )
        repository.complete_title_generation = AsyncMock()
        model = MagicMock()
        model.ainvoke = AsyncMock(return_value=AIMessage(content="华东销售趋势"))
        service = ConversationTitleService(model)

        await service.generate_and_update(
            repository,
            7,
            _CONVERSATION_ID,
            "查看华东销售趋势",
            "查看华东销售趋势",
        )

        repository.complete_title_generation.assert_awaited_once()
        self.assertEqual(
            repository.complete_title_generation.await_args.kwargs["title"],
            "华东销售趋势",
        )

    async def test_model_title_does_not_overwrite_manual_title(self) -> None:
        repository = MagicMock()
        repository.get = AsyncMock(
            return_value=_conversation("用户手动标题", title_pending=False)
        )
        repository.complete_title_generation = AsyncMock()
        model = MagicMock()
        model.ainvoke = AsyncMock(return_value=AIMessage(content="模型标题"))
        service = ConversationTitleService(model)

        await service.generate_and_update(
            repository,
            7,
            _CONVERSATION_ID,
            "初始标题",
            "原始消息",
        )

        repository.complete_title_generation.assert_not_awaited()

    async def test_create_uses_initial_message_title(self) -> None:
        repository = MagicMock()
        expected = _conversation("首条用户消息", title_pending=True)
        repository.create = AsyncMock(return_value=expected)
        current_user = MagicMock(id=7)

        response = await api_create_conversation(
            chat_schema.CreateConversationRequest(initial_message="  首条用户消息  "),
            repository,
            current_user,
        )

        repository.create.assert_awaited_once_with(
            7,
            "首条用户消息",
            is_draft=False,
        )
        self.assertEqual(response.title, "首条用户消息")

    async def test_first_stream_claims_background_title_generation(self) -> None:
        repository = MagicMock()
        pending = _conversation("新对话", title_pending=True, is_draft=True)
        claimed = pending.model_copy(
            update={
                "title": "分析华北区域销售额",
                "title_source": "分析华北区域销售额",
                "is_draft": False,
            }
        )
        repository.get = AsyncMock(return_value=pending)
        repository.claim_title_generation = AsyncMock(return_value=claimed)
        request = chat_schema.ChatStreamRequest(
            conversation_id=_CONVERSATION_ID,
            message=chat_schema.UserMessageRequest(
                parts=[chat_schema.TextContent(type="text", text="分析华北区域销售额")],
            ),
        )
        current_user = MagicMock(id=7)

        lifecycle = MagicMock()
        lifecycle.lock.side_effect = _unlocked
        with patch(
            "app.analytics.api.chat.router.enqueue_conversation_title",
        ) as enqueue_title:
            await api_stream_chat(
                request,
                repository,
                current_user,
                lifecycle,
                MagicMock(),
                MagicMock(),
            )

        repository.claim_title_generation.assert_awaited_once_with(
            pending,
            title="分析华北区域销售额",
            source="分析华北区域销售额",
        )
        enqueue_title.assert_called_once_with(
            7,
            _CONVERSATION_ID,
            "分析华北区域销售额",
            "分析华北区域销售额",
        )


if __name__ == "__main__":
    unittest.main()
