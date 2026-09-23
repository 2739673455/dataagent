"""通过真实 Turn → Run 验证受理与执行共用生命周期锁。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.errors import (
    ConversationBusyError,
    ConversationNotFoundError,
    ConversationNotResumableError,
)
from app.assistant.events.schemas import TextContent, UserMessageRequest
from app.assistant.execution.run import ConversationRunService
from app.assistant.execution.turn import (
    ConversationTurnService,
)
from app.shared.errors.infrastructure import AdvisoryLockBusyError


class TurnAdmissionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conversation_id = uuid4()
        self.owner = None
        self.acquisitions = 0
        self.transaction_open = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

        @asynccontextmanager
        async def lock(name):
            if self.owner is not None:
                raise AdvisoryLockBusyError("busy")
            self.owner = asyncio.current_task()
            self.acquisitions += 1
            try:
                yield
            finally:
                self.owner = None

        @asynccontextmanager
        async def transaction():
            self.assertIs(self.owner, asyncio.current_task())
            self.transaction_open = True
            try:
                yield
            finally:
                self.transaction_open = False

        async def graph(**kwargs):
            self.assertIs(self.owner, asyncio.current_task())
            self.assertFalse(self.transaction_open)
            self.started.set()
            await self.release.wait()
            if False:
                yield

        @asynccontextmanager
        async def use_runtime(*args):
            yield MagicMock(planner=MagicMock(astream=graph))

        self.agents = MagicMock(use_runtime=use_runtime)
        self.locks = MagicMock(advisory_lock=lock)
        self.runs = self.new_worker()
        conversation = MagicMock(id=self.conversation_id, is_draft=True, title="未命名")
        self.repo = MagicMock(
            session=MagicMock(begin=transaction),
            get=AsyncMock(return_value=conversation),
            update=AsyncMock(return_value=conversation),
        )
        self.turn = ConversationTurnService(
            repository=self.repo, runs=self.runs, agents=self.agents
        )

    def new_worker(self):
        runs = ConversationRunService(self.agents, MagicMock(), MagicMock(), self.locks)
        self.addAsyncCleanup(runs.close)
        return runs

    async def test_start_holds_one_lock_and_duplicate_worker_cannot_update_directory(
        self,
    ):
        message = UserMessageRequest(parts=[TextContent(type="text", text="分析")])
        other = ConversationTurnService(
            repository=self.repo, runs=self.new_worker(), agents=self.agents
        )
        with patch(
            "app.assistant.execution.turn.enqueue_conversation_title"
        ) as enqueue:
            stream = await self.turn.start(1, self.conversation_id, message)
            await self.started.wait()
            with self.assertRaises(ConversationBusyError):
                await other.start(1, self.conversation_id, message)
            self.repo.update.assert_awaited_once()
            enqueue.assert_called_once()
            self.assertEqual(self.acquisitions, 1)
            self.release.set()
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertIsNone(self.owner)

    async def test_resume_rechecks_state_under_lock_before_execution(self):
        async def can_resume(*args):
            self.assertIs(self.owner, asyncio.current_task())
            self.assertFalse(self.transaction_open)
            return False

        with (
            patch.object(
                self.agents,
                "can_resume_planner",
                side_effect=can_resume,
            ),
            self.assertRaises(ConversationNotResumableError),
        ):
            await self.turn.resume(1, self.conversation_id)
        self.assertFalse(self.started.is_set())
        self.assertIsNone(self.owner)
        self.repo.update.assert_not_awaited()

    async def test_deleted_conversation_rejected_before_runtime_creation(self):
        self.repo.get.return_value = None
        with self.assertRaises(ConversationNotFoundError):
            await self.turn.resume(1, self.conversation_id)
        self.assertFalse(self.started.is_set())
        self.assertIsNone(self.owner)

    async def test_deletion_stops_run_before_marking_and_rejects_next_message(self):
        message = UserMessageRequest(parts=[TextContent(type="text", text="分析")])
        conversation = self.repo.get.return_value
        conversation.deletion_requested_at = None

        async def get(*args, include_deleting=False):
            if conversation.deletion_requested_at is not None and not include_deleting:
                return None
            return conversation

        async def update(row, **fields):
            for name, value in fields.items():
                setattr(row, name, value)
            return row

        @asynccontextmanager
        async def repository():
            yield self.repo

        self.repo.get.side_effect = get
        self.repo.update.side_effect = update
        lifecycle = ConversationLifecycleService(
            repository,
            MagicMock(),
            self.locks,
            self.agents,
            MagicMock(),
            MagicMock(),
            runs=self.runs,
        )
        with patch("app.assistant.execution.turn.enqueue_conversation_title"):
            stream = await self.turn.start(1, self.conversation_id, message)
            await self.started.wait()
            self.assertTrue(
                await lifecycle.request_conversation_deletion(1, self.conversation_id)
            )
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertIsNotNone(conversation.deletion_requested_at)
            with self.assertRaises(ConversationNotFoundError):
                await self.turn.start(1, self.conversation_id, message)
        self.assertIsNone(self.owner)
