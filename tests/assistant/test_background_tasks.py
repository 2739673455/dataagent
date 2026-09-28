"""应用内任务的重试、取消和周期补偿。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.assistant.tasks import ConversationTasks
from app.shared.config.app_config import LifecycleConfig
from app.shared.errors.infrastructure import AdvisoryLockBusyError


class BackgroundTasksTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        @asynccontextmanager
        async def lock(name):
            yield

        self.lifecycle = MagicMock(
            delete_conversation_resources=AsyncMock(),
            cleanup_pending_deletions=AsyncMock(),
            cleanup_expired_drafts=AsyncMock(),
        )
        self.persistence = MagicMock(advisory_lock=lock)
        self.tasks = ConversationTasks(
            MagicMock(),
            self.lifecycle,
            self.persistence,
            LifecycleConfig(draft_ttl_minutes=1440, cleanup_batch_size=100),
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

        self.tasks._config = self.tasks._config.model_copy(
            update={"task_timeout_seconds": 0.001}
        )
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

    async def test_busy_cleanup_lock_skips_scan(self):
        attempted = asyncio.Event()

        @asynccontextmanager
        async def lock(name):
            attempted.set()
            raise AdvisoryLockBusyError("busy")
            yield

        self.persistence.advisory_lock = lock
        self.tasks.start()
        await asyncio.wait_for(attempted.wait(), 1)
        self.lifecycle.cleanup_pending_deletions.assert_not_awaited()
        self.lifecycle.cleanup_expired_drafts.assert_not_awaited()

    async def test_title_commits_and_closes_scoped_resources(self):
        closed = []
        model = MagicMock()
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

        title_service = MagicMock(generate_and_update=AsyncMock())
        conversation_id = uuid4()
        with (
            patch.object(self.tasks._postgres, "session", session_scope),
            patch("app.assistant.tasks.create_configured_model", model_scope),
            patch(
                "app.assistant.tasks.ConversationTitleService",
                return_value=title_service,
            ),
        ):
            self.tasks.generate_title(1, conversation_id, "即时标题", "分析订单")
            await asyncio.gather(*self.tasks._tasks)
        args = title_service.generate_and_update.await_args.args
        self.assertEqual(args[1:], (1, conversation_id, "即时标题", "分析订单"))
        session.commit.assert_awaited_once()
        self.assertEqual(closed, ["session", "model"])
        self.assertFalse(self.tasks._tasks)
