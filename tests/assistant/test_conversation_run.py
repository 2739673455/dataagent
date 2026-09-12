"""后台回合取消行为测试，保留真实 Chat 流式调用链。"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from langchain_core.messages import AIMessageChunk

from app.assistant.agents.contracts import PlannerTurnContext
from app.assistant.contracts import chat as chat_schema
from app.assistant.services.conversation_run import ConversationRunService

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


class ConversationRunCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.execution_lock = asyncio.Lock()
        self.emit_delta = False

        async def stream(**kwargs: Any) -> AsyncGenerator[dict[str, Any]]:
            try:
                if self.emit_delta:
                    yield {
                        "type": "messages",
                        "data": (
                            AIMessageChunk(id="answer", content="开始"),
                            {},
                        ),
                    }
                self.started.set()
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            finally:
                self.cleaned.set()

        @asynccontextmanager
        async def execution(*args: Any, **kwargs: Any):
            async with self.execution_lock:
                yield PlannerTurnContext(
                    user_id=1,
                    conversation_id=_CONVERSATION_ID,
                    max_continuations=0,
                )

        runtime = MagicMock()
        runtime.planner.astream = stream
        manager = MagicMock()
        manager.get_conversation_runtime = AsyncMock(return_value=runtime)
        manager.execution = execution
        self.service = ConversationRunService(manager, MagicMock())
        self.addAsyncCleanup(self.service.close)

    async def test_stop_interrupts_model_wait_and_finishes_all_subscribers(
        self,
    ) -> None:
        async with asyncio.timeout(3):
            stream = await self.service.start_turn(
                1,
                _CONVERSATION_ID,
                chat_schema.UserMessageRequest(
                    parts=[chat_schema.TextContent(type="text", text="分析")]
                ),
            )
            await self.started.wait()
            second = await self.service.subscribe(1, _CONVERSATION_ID)
            self.assertTrue(await self.service.stop(1, _CONVERSATION_ID))
            self.assertTrue(self.cancelled.is_set())
            self.assertTrue(self.cleaned.is_set())
            self.assertFalse(self.execution_lock.locked())
            self.assertFalse(await self.service.is_running(1, _CONVERSATION_ID))
            for subscription in (stream, second):
                self.assertEqual([event.type async for event in subscription], ["done"])
            self.assertFalse(await self.service.stop(1, _CONVERSATION_ID))

    async def test_stop_after_delta_allows_same_conversation_to_restart(self) -> None:
        async with asyncio.timeout(3):
            self.emit_delta = True
            stream = await self.service.resume_turn(1, _CONVERSATION_ID)
            first = await anext(stream)
            self.assertIsInstance(first, chat_schema.ChatStreamMessageDeltaEvent)
            await self.started.wait()
            self.assertTrue(await self.service.stop(1, _CONVERSATION_ID))
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertTrue(self.cancelled.is_set())
            self.assertTrue(self.cleaned.is_set())
            self.assertFalse(self.execution_lock.locked())

            self.started.clear()
            self.cleaned.clear()
            self.cancelled.clear()
            resumed = await self.service.resume_turn(1, _CONVERSATION_ID)
            await self.started.wait()
            self.release.set()
            self.assertEqual(
                [event.type async for event in resumed], ["message_delta", "done"]
            )
            self.assertTrue(self.cleaned.is_set())
            self.assertFalse(self.cancelled.is_set())
            self.assertFalse(await self.service.is_running(1, _CONVERSATION_ID))
            self.assertFalse(self.execution_lock.locked())

    async def test_close_interrupts_running_turn_and_finishes_subscription(
        self,
    ) -> None:
        async with asyncio.timeout(3):
            stream = await self.service.resume_turn(1, _CONVERSATION_ID)
            await self.started.wait()
            await self.service.close()
            self.assertTrue(self.cancelled.is_set())
            self.assertTrue(self.cleaned.is_set())
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertEqual(await self.service.running_conversation_ids(1), set())
