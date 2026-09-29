"""应用内任务的重试、取消和周期补偿。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.assistant.tasks import ConversationTasks


class BackgroundTasksTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifecycle = MagicMock(
            delete_conversation_resources=AsyncMock(),
            cleanup_pending_deletions=AsyncMock(),
            cleanup_expired_drafts=AsyncMock(),
        )
        self.tasks = ConversationTasks(
            MagicMock(),
            self.lifecycle,
        )
        self.addAsyncCleanup(self.tasks.close)

    async def test_transient_failure_retries_and_stops_on_success(self):
        operation = AsyncMock(side_effect=[RuntimeError("temporary"), None])
        with patch(
            "app.assistant.tasks.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            await self.tasks._run("test", operation)
        self.assertEqual(operation.await_count, 2)
        sleep.assert_awaited_once_with(1)

    async def test_permanent_failure_is_bounded(self):
        operation = AsyncMock(side_effect=RuntimeError("unavailable"))
        with patch(
            "app.assistant.tasks.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            await self.tasks._run("test", operation)
        self.assertEqual(operation.await_count, 4)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [1, 2, 4])

    async def test_timeout_cancels_operation_before_retry(self):
        released = []

        async def operation():
            try:
                await asyncio.Event().wait()
            finally:
                released.append(True)

        timeout = patch("app.assistant.tasks._TASK_TIMEOUT_SECONDS", 0.001)
        timeout.start()
        self.addCleanup(timeout.stop)
        with patch("app.assistant.tasks.asyncio.sleep", new_callable=AsyncMock):
            await self.tasks._run("timeout", operation)
        self.assertEqual(len(released), 4)

    async def test_shutdown_waits_for_operation_cleanup_without_retry(self):
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def delete(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        self.lifecycle.delete_conversation_resources.side_effect = delete
        self.tasks.delete_conversation(1, uuid4())
        await started.wait()
        await self.tasks.close()
        self.assertTrue(cleaned.is_set())
        self.lifecycle.delete_conversation_resources.assert_awaited_once()
        self.assertFalse(self.tasks._tasks)
        with self.assertRaises(RuntimeError):
            self.tasks.delete_conversation(1, uuid4())

    async def test_startup_scans_pending_deletions_and_expired_drafts(self):
        scanned = asyncio.Event()
        self.lifecycle.cleanup_expired_drafts.side_effect = scanned.set
        self.tasks.start()
        await asyncio.wait_for(scanned.wait(), 1)
        self.lifecycle.cleanup_pending_deletions.assert_awaited_once()
        self.lifecycle.cleanup_expired_drafts.assert_awaited_once()

    async def test_title_commits_and_closes_scoped_resources(self):
        closed = []
        model = MagicMock(
            ainvoke=AsyncMock(return_value=AIMessage(content="  订单分析\n  "))
        )
        session = MagicMock(commit=AsyncMock())

        @asynccontextmanager
        async def model_scope(*args):
            try:
                yield model
            finally:
                closed.append("model")

        @asynccontextmanager
        async def session_scope():
            try:
                yield session
            finally:
                closed.append("session")

        repo = MagicMock(update=AsyncMock())
        conversation_id = uuid4()
        with (
            patch.object(self.tasks._postgres, "session", session_scope),
            patch("app.assistant.services.title.create_configured_model", model_scope),
            patch(
                "app.assistant.services.title.ConversationPGRepo",
                return_value=repo,
            ),
        ):
            self.tasks.generate_title(1, conversation_id, "分析订单")
            await asyncio.gather(*self.tasks._tasks)
        model.ainvoke.assert_awaited_once()
        self.assertEqual(model.ainvoke.await_args.args[0][1].content, "分析订单")
        repo.update.assert_awaited_once_with(1, conversation_id, title="订单分析")
        session.commit.assert_awaited_once()
        self.assertEqual(closed, ["model", "session"])
        self.assertFalse(self.tasks._tasks)

    async def test_title_failure_does_not_retry_or_open_database(self):
        model = MagicMock(ainvoke=AsyncMock(side_effect=RuntimeError("unavailable")))

        @asynccontextmanager
        async def model_scope(*args):
            yield model

        with patch("app.assistant.services.title.create_configured_model", model_scope):
            self.tasks.generate_title(1, uuid4(), "分析订单")
            await asyncio.gather(*self.tasks._tasks)
        model.ainvoke.assert_awaited_once()
        self.tasks._postgres.session.assert_not_called()

    async def test_empty_title_keeps_initial_title(self):
        model = MagicMock(ainvoke=AsyncMock(return_value=AIMessage(content=" \n\t ")))

        @asynccontextmanager
        async def model_scope(*args):
            yield model

        with patch("app.assistant.services.title.create_configured_model", model_scope):
            self.tasks.generate_title(1, uuid4(), "分析订单")
            await asyncio.gather(*self.tasks._tasks)
        self.tasks._postgres.session.assert_not_called()

    async def test_title_timeout_closes_model_without_retry(self):
        closed = []

        @asynccontextmanager
        async def model_scope(*args):
            try:
                yield model
            finally:
                closed.append(True)

        async def invoke(*args):
            await asyncio.Event().wait()

        model = MagicMock(ainvoke=AsyncMock(side_effect=invoke))
        timeout = patch("app.assistant.tasks._TASK_TIMEOUT_SECONDS", 0.001)
        timeout.start()
        self.addCleanup(timeout.stop)
        with patch("app.assistant.services.title.create_configured_model", model_scope):
            self.tasks.generate_title(1, uuid4(), "分析订单")
            await asyncio.gather(*self.tasks._tasks)
        model.ainvoke.assert_awaited_once()
        self.assertEqual(closed, [True])
        self.tasks._postgres.session.assert_not_called()

    async def test_title_submission_during_shutdown_does_not_fail_caller(self):
        await self.tasks.close()
        self.tasks.generate_title(1, uuid4(), "分析订单")
        self.assertFalse(self.tasks._tasks)
