"""运行中删除会话时，先隐藏、等待执行退出，再清理资源。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.assistant.services.lifecycle import ConversationLifecycleService


class ConversationDeletionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conversation_id = uuid4()
        self.events = []
        self.conversation = MagicMock(deletion_requested_at=None)

        async def update(user_id, conversation_id, **fields):
            self.conversation.deletion_requested_at = fields["deletion_requested_at"]
            self.events.append("marked")

        self.repo = MagicMock(
            get=AsyncMock(return_value=self.conversation),
            update=AsyncMock(side_effect=update),
            delete=AsyncMock(),
        )

        @asynccontextmanager
        async def repository():
            yield self.repo
            self.events.append("committed")

        async def stop(*args):
            self.assertIsNotNone(self.conversation.deletion_requested_at)
            self.assertIn("committed", self.events)
            self.events.append("stopped")

        self.runs = MagicMock(stop=AsyncMock(side_effect=stop))
        self.agents = MagicMock(delete_conversation_state=AsyncMock())
        self.sandbox = MagicMock(delete_conversation=AsyncMock())
        self.service = ConversationLifecycleService(
            repository, self.agents, self.sandbox, self.runs
        )

    async def test_deletion_marks_and_commits_before_stopping_run(self):
        self.assertTrue(
            await self.service.request_conversation_deletion(1, self.conversation_id)
        )
        self.assertEqual(self.events, ["marked", "committed", "stopped"])
        self.sandbox.delete_conversation.assert_not_awaited()

    async def test_missing_or_foreign_conversation_does_not_stop_run(self):
        self.repo.get.return_value = None
        self.assertFalse(
            await self.service.request_conversation_deletion(1, self.conversation_id)
        )
        self.runs.stop.assert_not_awaited()

    async def test_cleanup_waits_for_run_exit_and_serializes_duplicate_deletes(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def stop(*args):
            entered.set()
            await release.wait()

        self.runs.stop.side_effect = stop
        self.repo.delete.side_effect = lambda *_: setattr(
            self.repo.get, "return_value", None
        )
        first = asyncio.create_task(
            self.service.delete_conversation_resources(1, self.conversation_id)
        )
        await entered.wait()
        second = asyncio.create_task(
            self.service.delete_conversation_resources(1, self.conversation_id)
        )
        await asyncio.sleep(0)
        self.agents.delete_conversation_state.assert_not_awaited()
        self.sandbox.delete_conversation.assert_not_awaited()
        release.set()
        self.assertEqual(await asyncio.gather(first, second), [True, False])
        self.agents.delete_conversation_state.assert_awaited_once()
        self.sandbox.delete_conversation.assert_awaited_once()

    async def test_cleanup_failure_keeps_deletion_marker_for_next_scan(self):
        self.agents.delete_conversation_state.side_effect = RuntimeError("checkpoint")
        with self.assertRaisesRegex(RuntimeError, "checkpoint"):
            await self.service.delete_conversation_resources(1, self.conversation_id)
        self.assertIsNotNone(self.conversation.deletion_requested_at)
        self.repo.delete.assert_not_awaited()
        self.sandbox.delete_conversation.assert_not_awaited()
        self.agents.delete_conversation_state.side_effect = None
        await self.service.delete_conversation_resources(1, self.conversation_id)
        self.repo.update.assert_awaited_once()
        self.sandbox.delete_conversation.assert_awaited_once()
        self.repo.delete.assert_awaited_once_with(1, self.conversation_id)
