"""通过真实 Turn → Run 验证单进程受理互斥、事务范围及运行中删除。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.assistant.errors import (
    ConversationBusyError,
    ConversationNotFoundError,
    ConversationNotResumableError,
    ConversationRunConflictError,
)
from app.assistant.models.chat import TextContent, UserMessageRequest
from app.assistant.services.lifecycle import ConversationLifecycleService
from app.assistant.services.run import ConversationRunService
from app.assistant.services.turn import (
    ConversationTurnService,
)


class TurnAdmissionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conversation_id = uuid4()
        self.transaction_open = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

        @asynccontextmanager
        async def transaction():
            self.transaction_open = True
            try:
                yield
            finally:
                self.transaction_open = False

        async def graph(**kwargs):
            self.assertFalse(self.transaction_open)
            self.started.set()
            await self.release.wait()
            if False:
                yield

        async def create_planner(*args):
            return MagicMock(astream=graph)

        self.agents = MagicMock(create_planner=create_planner)
        self.runs = self.new_worker()
        conversation = MagicMock(id=self.conversation_id, is_draft=True, title="未命名")
        self.repo = MagicMock(
            session=MagicMock(begin=transaction),
            get=AsyncMock(return_value=conversation),
            update=AsyncMock(return_value=conversation),
        )
        self.tasks = MagicMock()
        self.turn = ConversationTurnService(
            repository=self.repo, runs=self.runs, agents=self.agents, tasks=self.tasks
        )

    def new_worker(self):
        runs = ConversationRunService(self.agents, MagicMock())
        self.addAsyncCleanup(runs.close)
        return runs

    async def test_duplicate_request_cannot_update_directory(
        self,
    ):
        message = UserMessageRequest(parts=[TextContent(type="text", text="分析")])
        other = ConversationTurnService(
            repository=self.repo,
            runs=self.runs,
            agents=self.agents,
            tasks=self.tasks,
        )
        with patch.object(self.tasks, "generate_title") as enqueue:
            stream = await self.turn.start(1, self.conversation_id, message)
            await self.started.wait()
            with self.assertRaises(ConversationRunConflictError):
                await other.start(1, self.conversation_id, message)
            self.repo.update.assert_awaited_once()
            enqueue.assert_called_once()
            self.release.set()
            self.assertEqual([event.type async for event in stream], ["done"])

    async def test_resume_rechecks_state_before_execution(self):
        async def can_resume(*args):
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
        self.repo.update.assert_not_awaited()

    async def test_deleted_conversation_rejected_before_runtime_creation(self):
        self.repo.get.return_value = None
        with self.assertRaises(ConversationNotFoundError):
            await self.turn.resume(1, self.conversation_id)
        self.assertFalse(self.started.is_set())

    async def test_deletion_stops_run_and_rejects_next_message(self):
        message = UserMessageRequest(parts=[TextContent(type="text", text="分析")])
        conversation = self.repo.get.return_value
        conversation.deletion_requested_at = None

        async def get(*args, include_deleting=False):
            if conversation.deletion_requested_at is not None and not include_deleting:
                return None
            return conversation

        async def update(user_id, conversation_id, **fields):
            for name, value in fields.items():
                setattr(conversation, name, value)

        @asynccontextmanager
        async def repository():
            yield self.repo

        self.repo.get.side_effect = get
        self.repo.update.side_effect = update
        lifecycle = ConversationLifecycleService(
            repository,
            self.agents,
            MagicMock(),
            runs=self.runs,
        )
        with patch.object(self.tasks, "generate_title"):
            stream = await self.turn.start(1, self.conversation_id, message)
            await self.started.wait()
            self.assertTrue(
                await lifecycle.request_conversation_deletion(1, self.conversation_id)
            )
            self.assertEqual([event.type async for event in stream], ["done"])
            self.assertIsNotNone(conversation.deletion_requested_at)
            with self.assertRaises(ConversationNotFoundError):
                await self.turn.start(1, self.conversation_id, message)

    async def test_deletion_cancels_admission_before_agent_is_created(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def read_conversation(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        self.repo.get.side_effect = read_conversation
        deletion_repo = MagicMock(
            get=AsyncMock(return_value=MagicMock(deletion_requested_at=None)),
            update=AsyncMock(),
        )

        @asynccontextmanager
        async def repository():
            yield deletion_repo

        lifecycle = ConversationLifecycleService(
            repository, self.agents, MagicMock(), self.runs
        )
        async with asyncio.timeout(1):
            request = asyncio.create_task(
                self.turn.start(
                    1,
                    self.conversation_id,
                    UserMessageRequest(parts=[TextContent(type="text", text="分析")]),
                )
            )
            await entered.wait()
            await lifecycle.request_conversation_deletion(1, self.conversation_id)
            with self.assertRaises(ConversationBusyError):
                await request
        self.assertTrue(cleaned.is_set())
        self.assertFalse(self.started.is_set())
        self.tasks.generate_title.assert_not_called()
        self.repo.update.assert_not_awaited()
