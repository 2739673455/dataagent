"""Conversation Run 与 SSE 订阅解耦测试。"""

import asyncio
import unittest
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import patch
from uuid import UUID

from app.assistant.api.chat import schemas as chat_schema
from app.assistant.services import chat as chat_service
from app.assistant.services.contracts import (
    AgentRuntimeManager,
    ConversationFileInspector,
)
from app.assistant.services.conversation_run import ConversationRunService

_CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000321")


class ConversationRunServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = ConversationRunService(
            cast(AgentRuntimeManager, object()),
            cast(ConversationFileInspector, object()),
        )
        self.addAsyncCleanup(self.service.close)

    async def test_subscription_disconnect_does_not_cancel_run(self) -> None:
        release = asyncio.Event()
        finished = asyncio.Event()

        async def run_turn(
            *args: object, **kwargs: object
        ) -> AsyncIterator[chat_schema.ChatStreamEventPayload]:
            del args, kwargs
            try:
                yield chat_schema.ChatStreamMessageEvent(
                    type="message",
                    message=chat_schema.MessageResponse(
                        message_id="assistant-1",
                        role="assistant",
                        parts=[chat_schema.TextContent(type="text", text="第一步")],
                    ),
                )
                await release.wait()
                yield chat_schema.ChatStreamMessageEvent(
                    type="message",
                    message=chat_schema.MessageResponse(
                        message_id="assistant-2",
                        role="assistant",
                        parts=[chat_schema.TextContent(type="text", text="最终结果")],
                    ),
                )
            finally:
                finished.set()

        with patch.object(chat_service, "run_agent_turn", new=run_turn):
            subscription = await self.service.start_turn(
                7,
                _CONVERSATION_ID,
                chat_schema.UserMessageRequest(
                    parts=[chat_schema.TextContent(type="text", text="分析")]
                ),
            )
            first = await anext(subscription)
            self.assertIsInstance(first, chat_schema.ChatStreamMessageEvent)
            await subscription.aclose()

            self.assertTrue(await self.service.is_running(7, _CONVERSATION_ID))
            self.assertEqual(
                await self.service.running_conversation_ids(7),
                {_CONVERSATION_ID},
            )
            self.assertEqual(await self.service.running_conversation_ids(8), set())
            self.assertFalse(finished.is_set())

            reconnected = await self.service.subscribe(7, _CONVERSATION_ID)
            replayed = await anext(reconnected)
            self.assertIsInstance(replayed, chat_schema.ChatStreamMessageEvent)
            release.set()
            second = await anext(reconnected)
            done = await anext(reconnected)
            self.assertIsInstance(second, chat_schema.ChatStreamMessageEvent)
            self.assertIsInstance(done, chat_schema.ChatStreamDoneEvent)
            with self.assertRaises(StopAsyncIteration):
                await anext(reconnected)

        self.assertTrue(finished.is_set())
        self.assertFalse(await self.service.is_running(7, _CONVERSATION_ID))
        self.assertEqual(await self.service.running_conversation_ids(7), set())

    async def test_stop_is_the_explicit_run_cancellation_path(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def run_turn(
            *args: object, **kwargs: object
        ) -> AsyncIterator[chat_schema.ChatStreamEventPayload]:
            del args, kwargs
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            if False:
                yield chat_schema.ChatStreamDoneEvent(type="done")

        with patch.object(chat_service, "run_agent_turn", new=run_turn):
            subscription = await self.service.start_turn(
                7,
                _CONVERSATION_ID,
                chat_schema.UserMessageRequest(
                    parts=[chat_schema.TextContent(type="text", text="分析")]
                ),
            )
            await started.wait()
            self.assertTrue(await self.service.stop(7, _CONVERSATION_ID))
            done = await anext(subscription)
            self.assertIsInstance(done, chat_schema.ChatStreamDoneEvent)

        self.assertTrue(cancelled.is_set())
        self.assertFalse(await self.service.is_running(7, _CONVERSATION_ID))
