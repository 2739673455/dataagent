"""后台回合取消行为测试，保留真实 Chat 流式调用链。"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from langchain_core.messages import AIMessageChunk

from app.assistant.errors import ConversationBusyError, ConversationRunConflictError
from app.assistant.events import schemas as chat_schema
from app.assistant.execution.run import (
    ConversationRunService,
)

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


class ConversationRunCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.execution_lock = asyncio.Lock()
        self.emit_delta = False
        self.fail_execution = False

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
                if self.fail_execution:
                    raise RuntimeError("model failed")
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            finally:
                self.cleaned.set()

        @asynccontextmanager
        async def lock(name):
            async with self.execution_lock:
                yield

        @asynccontextmanager
        async def use_runtime(*args):
            yield runtime

        runtime = MagicMock()
        runtime.planner.astream = stream
        manager = MagicMock(use_runtime=use_runtime)
        self.service = ConversationRunService(
            manager,
            MagicMock(),
            recall=MagicMock(),
            locks=MagicMock(advisory_lock=lock),
        )
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
                prepare=AsyncMock(),
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
            stream = await self.service.resume_turn(
                1, _CONVERSATION_ID, prepare=AsyncMock()
            )
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
            resumed = await self.service.resume_turn(
                1, _CONVERSATION_ID, prepare=AsyncMock()
            )
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
            stream = await self.service.resume_turn(
                1, _CONVERSATION_ID, prepare=AsyncMock()
            )
            await self.started.wait()
            await self.service.close()
            self.assertTrue(self.cancelled.is_set())
            self.assertTrue(self.cleaned.is_set())
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertEqual(await self.service.running_conversation_ids(1), set())

    async def test_cancel_before_background_task_starts_returns_to_caller(self) -> None:
        original = asyncio.create_task

        def cancel_new_run(coroutine, **kwargs):
            task = original(coroutine, **kwargs)
            if kwargs.get("name", "").startswith("conversation-run:"):
                task.cancel()
            return task

        async with asyncio.timeout(1):
            with (
                patch(
                    "app.assistant.execution.run.asyncio.create_task",
                    side_effect=cancel_new_run,
                ),
                self.assertRaises(ConversationBusyError),
            ):
                await self.service.resume_turn(1, _CONVERSATION_ID, prepare=AsyncMock())
            self.assertFalse(self.started.is_set())
            stream = await self.service.subscribe(1, _CONVERSATION_ID)
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertFalse(await self.service.is_running(1, _CONVERSATION_ID))

    async def test_close_during_admission_finishes_waiting_request(self) -> None:
        entered = asyncio.Event()

        async def prepare():
            entered.set()
            await asyncio.Event().wait()

        async with asyncio.timeout(1):
            request = asyncio.create_task(
                self.service.resume_turn(1, _CONVERSATION_ID, prepare=prepare)
            )
            await entered.wait()
            await self.service.close()
            with self.assertRaises(ConversationBusyError):
                await request
            self.assertFalse(self.started.is_set())
            stream = await self.service.subscribe(1, _CONVERSATION_ID)
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertFalse(self.execution_lock.locked())

    async def test_execution_error_cleans_up_before_terminal_events(self) -> None:
        async with asyncio.timeout(1):
            self.fail_execution = True
            stream = await self.service.resume_turn(
                1, _CONVERSATION_ID, prepare=AsyncMock()
            )
            await self.started.wait()
            self.release.set()
            self.assertEqual([event.type async for event in stream], ["error", "done"])
            self.assertTrue(self.cleaned.is_set())
            self.assertFalse(self.execution_lock.locked())
            self.assertFalse(await self.service.is_running(1, _CONVERSATION_ID))
            await self.service.close()

    async def test_disconnecting_subscriber_does_not_stop_execution(self) -> None:
        async with asyncio.timeout(1):
            self.emit_delta = True
            stream = await self.service.resume_turn(
                1, _CONVERSATION_ID, prepare=AsyncMock()
            )
            self.assertEqual((await anext(stream)).type, "message_delta")
            await stream.aclose()
            self.assertTrue(await self.service.is_running(1, _CONVERSATION_ID))
            self.assertFalse(self.cancelled.is_set())
            reconnected = await self.service.subscribe(1, _CONVERSATION_ID)
            self.release.set()
            self.assertEqual(
                [event.type async for event in reconnected], ["message_delta", "done"]
            )
            self.assertTrue(self.cleaned.is_set())

    async def test_duplicate_request_cannot_prepare_while_first_is_being_admitted(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def prepare():
            self.assertTrue(self.execution_lock.locked())
            entered.set()
            await release.wait()

        async with asyncio.timeout(1):
            first = asyncio.create_task(
                self.service.resume_turn(1, _CONVERSATION_ID, prepare=prepare)
            )
            await entered.wait()
            duplicate = AsyncMock()
            with self.assertRaises(ConversationRunConflictError):
                await self.service.resume_turn(1, _CONVERSATION_ID, prepare=duplicate)
            duplicate.assert_not_awaited()
            release.set()
            stream = await first
            await self.started.wait()
            self.assertTrue(self.execution_lock.locked())
            await self.service.stop(1, _CONVERSATION_ID)
            self.assertEqual([event.type async for event in stream], ["done"])

    async def test_admission_failure_returns_original_error_without_execution(self):
        failure = ValueError("conversation missing")
        with self.assertRaises(ValueError) as caught:
            await self.service.resume_turn(
                1, _CONVERSATION_ID, prepare=AsyncMock(side_effect=failure)
            )
        self.assertIs(caught.exception, failure)
        self.assertFalse(self.started.is_set())
        self.assertFalse(self.execution_lock.locked())
        self.assertFalse(await self.service.is_running(1, _CONVERSATION_ID))

    async def test_request_cancel_waits_for_admission_cleanup(self):
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        async def prepare():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        async with asyncio.timeout(1):
            request = asyncio.create_task(
                self.service.resume_turn(1, _CONVERSATION_ID, prepare=prepare)
            )
            await entered.wait()
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertTrue(cleaned.is_set())
            self.assertFalse(self.execution_lock.locked())
            self.assertFalse(self.started.is_set())
