"""后台回合取消行为测试，保留真实 Chat 流式调用链。"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from langchain_core.messages import AIMessageChunk

from app.assistant.errors import ConversationBusyError, ConversationRunConflictError
from app.assistant.models import chat as chat_schema
from app.assistant.services.run import (
    ConversationRunService,
)

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


class ConversationRunCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleaned = asyncio.Event()
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

        async def create_planner(*args):
            return runtime

        runtime = MagicMock()
        runtime.astream = stream
        manager = MagicMock(create_planner=create_planner)
        self.service = ConversationRunService(
            manager,
            MagicMock(),
        )
        self.addAsyncCleanup(self.service.close)

    async def test_stop_interrupts_model_wait_and_finishes_all_subscribers(
        self,
    ) -> None:
        async with asyncio.timeout(3):
            stream = await self.service.start(
                1,
                _CONVERSATION_ID,
                chat_schema.UserMessageRequest(
                    parts=[chat_schema.TextContent(type="text", text="分析")]
                ),
                prepare=AsyncMock(),
            )
            await self.started.wait()
            second = self.service.subscribe(1, _CONVERSATION_ID)
            self.assertTrue(await self.service.stop(1, _CONVERSATION_ID))
            self.assertTrue(self.cancelled.is_set())
            self.assertTrue(self.cleaned.is_set())
            self.assertFalse(self.service.is_running(1, _CONVERSATION_ID))
            for subscription in (stream, second):
                self.assertEqual([event.type async for event in subscription], ["done"])
            self.assertFalse(await self.service.stop(1, _CONVERSATION_ID))

    async def test_stop_after_delta_allows_same_conversation_to_restart(self) -> None:
        async with asyncio.timeout(3):
            self.emit_delta = True
            stream = await self.service.start(
                1, _CONVERSATION_ID, None, prepare=AsyncMock()
            )
            first = await anext(stream)
            self.assertIsInstance(first, chat_schema.ChatStreamMessageDeltaEvent)
            await self.started.wait()
            self.assertTrue(await self.service.stop(1, _CONVERSATION_ID))
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertTrue(self.cancelled.is_set())
            self.assertTrue(self.cleaned.is_set())

            self.started.clear()
            self.cleaned.clear()
            self.cancelled.clear()
            resumed = await self.service.start(
                1, _CONVERSATION_ID, None, prepare=AsyncMock()
            )
            await self.started.wait()
            self.release.set()
            self.assertEqual(
                [event.type async for event in resumed], ["message_delta", "done"]
            )
            self.assertTrue(self.cleaned.is_set())
            self.assertFalse(self.cancelled.is_set())
            self.assertFalse(self.service.is_running(1, _CONVERSATION_ID))

    async def test_close_interrupts_running_turn_and_finishes_subscription(
        self,
    ) -> None:
        async with asyncio.timeout(3):
            stream = await self.service.start(
                1, _CONVERSATION_ID, None, prepare=AsyncMock()
            )
            await self.started.wait()
            await self.service.close()
            self.assertTrue(self.cancelled.is_set())
            self.assertTrue(self.cleaned.is_set())
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertEqual(self.service.running_conversation_ids(1), set())

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
                    "app.assistant.services.run.asyncio.create_task",
                    side_effect=cancel_new_run,
                ),
                self.assertRaises(ConversationBusyError),
            ):
                await self.service.start(1, _CONVERSATION_ID, None, prepare=AsyncMock())
            self.assertFalse(self.started.is_set())
            stream = self.service.subscribe(1, _CONVERSATION_ID)
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertFalse(self.service.is_running(1, _CONVERSATION_ID))

    async def test_close_during_admission_finishes_waiting_request(self) -> None:
        entered = asyncio.Event()

        async def prepare():
            entered.set()
            await asyncio.Event().wait()

        async with asyncio.timeout(1):
            request = asyncio.create_task(
                self.service.start(1, _CONVERSATION_ID, None, prepare=prepare)
            )
            await entered.wait()
            await self.service.close()
            with self.assertRaises(ConversationBusyError):
                await request
            self.assertFalse(self.started.is_set())
            stream = self.service.subscribe(1, _CONVERSATION_ID)
            self.assertEqual([event.type async for event in stream], ["done"])

    async def test_execution_error_cleans_up_before_terminal_events(self) -> None:
        async with asyncio.timeout(1):
            self.fail_execution = True
            stream = await self.service.start(
                1, _CONVERSATION_ID, None, prepare=AsyncMock()
            )
            await self.started.wait()
            self.release.set()
            self.assertEqual([event.type async for event in stream], ["error", "done"])
            self.assertTrue(self.cleaned.is_set())
            self.assertFalse(self.service.is_running(1, _CONVERSATION_ID))
            await self.service.close()

    async def test_disconnecting_subscriber_does_not_stop_execution(self) -> None:
        async with asyncio.timeout(1):
            self.emit_delta = True
            stream = await self.service.start(
                1, _CONVERSATION_ID, None, prepare=AsyncMock()
            )
            self.assertEqual((await anext(stream)).type, "message_delta")
            await stream.aclose()
            self.assertTrue(self.service.is_running(1, _CONVERSATION_ID))
            self.assertFalse(self.cancelled.is_set())
            reconnected = self.service.subscribe(1, _CONVERSATION_ID)
            self.release.set()
            self.assertEqual(
                [event.type async for event in reconnected], ["message_delta", "done"]
            )
            self.assertTrue(self.cleaned.is_set())

    async def test_duplicate_request_cannot_prepare_while_first_is_being_admitted(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def prepare():
            self.assertTrue(self.service.is_running(1, _CONVERSATION_ID))
            entered.set()
            await release.wait()

        async with asyncio.timeout(1):
            first = asyncio.create_task(
                self.service.start(1, _CONVERSATION_ID, None, prepare=prepare)
            )
            await entered.wait()
            duplicate = AsyncMock()
            with self.assertRaises(ConversationRunConflictError):
                await self.service.start(1, _CONVERSATION_ID, None, prepare=duplicate)
            duplicate.assert_not_awaited()
            release.set()
            stream = await first
            await self.started.wait()
            self.assertTrue(self.service.is_running(1, _CONVERSATION_ID))
            await self.service.stop(1, _CONVERSATION_ID)
            self.assertEqual([event.type async for event in stream], ["done"])

    async def test_admission_failure_returns_original_error_without_execution(self):
        failure = ValueError("conversation missing")
        with self.assertRaises(ValueError) as caught:
            await self.service.start(
                1, _CONVERSATION_ID, None, prepare=AsyncMock(side_effect=failure)
            )
        self.assertIs(caught.exception, failure)
        self.assertFalse(self.started.is_set())
        self.assertFalse(self.service.is_running(1, _CONVERSATION_ID))

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
                self.service.start(1, _CONVERSATION_ID, None, prepare=prepare)
            )
            await entered.wait()
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertTrue(cleaned.is_set())
            self.assertFalse(self.started.is_set())

    async def test_waiting_readers_receive_updates_and_completion(self):
        async with asyncio.timeout(1):
            first = await self.service.start(
                1, _CONVERSATION_ID, None, prepare=AsyncMock()
            )
            second = self.service.subscribe(1, _CONVERSATION_ID)
            run = self.service._runs[(1, _CONVERSATION_ID)]
            for delta in ("第一段", "第二段"):
                readers = [asyncio.create_task(anext(s)) for s in (first, second)]
                await asyncio.sleep(0)
                event = chat_schema.ChatStreamMessageDeltaEvent(
                    type="message_delta", message_id="answer", delta=delta
                )
                self.service._publish(run, event)
                self.assertEqual(await asyncio.gather(*readers), [event, event])
            readers = [asyncio.create_task(anext(s)) for s in (first, second)]
            await asyncio.sleep(0)
            self.release.set()
            self.assertEqual(
                [e.type for e in await asyncio.gather(*readers)], ["done", "done"]
            )
            for stream in (first, second):
                self.assertEqual([event async for event in stream], [])

    async def test_cancelled_reader_does_not_affect_other_reader(self):
        async with asyncio.timeout(1):
            first = await self.service.start(
                1, _CONVERSATION_ID, None, prepare=AsyncMock()
            )
            second = self.service.subscribe(1, _CONVERSATION_ID)
            waiting = asyncio.create_task(anext(first))
            remaining = asyncio.create_task(anext(second))
            await asyncio.sleep(0)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            self.assertTrue(self.service.is_running(1, _CONVERSATION_ID))
            self.release.set()
            self.assertEqual((await remaining).type, "done")
            await second.aclose()

    async def test_eviction_disconnects_lagging_reader_and_replays_retained_events(
        self,
    ):
        event = chat_schema.ChatStreamMessageDeltaEvent(
            type="message_delta", message_id="answer", delta="中文增量"
        )
        event_bytes = len(event.model_dump_json().encode("utf-8"))
        for count_limit, byte_limit, retained in (
            (2, 10000, 2),
            (512, event_bytes * 2, 2),
            (512, event_bytes - 1, 0),
        ):
            with (
                self.subTest(count_limit=count_limit, byte_limit=byte_limit),
                patch("app.assistant.services.run._REPLAY_EVENT_LIMIT", count_limit),
                patch("app.assistant.services.run._REPLAY_BYTE_LIMIT", byte_limit),
            ):
                async with asyncio.timeout(1):
                    slow = await self.service.start(
                        1, _CONVERSATION_ID, None, prepare=AsyncMock()
                    )
                    fast = self.service.subscribe(1, _CONVERSATION_ID)
                    run = self.service._runs[(1, _CONVERSATION_ID)]
                    for _ in range(3):
                        self.service._publish(run, event)
                        if retained:
                            self.assertEqual(await anext(fast), event)
                    self.assertEqual([e.type async for e in slow], ["error"])
                    reconnected = self.service.subscribe(1, _CONVERSATION_ID)
                    await self.service.stop(1, _CONVERSATION_ID)
                    self.assertEqual(
                        [e.type async for e in reconnected],
                        ["message_delta"] * retained + ["done"],
                    )
                    self.assertEqual(
                        [e.type async for e in fast],
                        ["done"] if retained else ["error"],
                    )
