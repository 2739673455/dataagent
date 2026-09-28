"""Run 独占运行时，构建与执行取消后不保留内存状态。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.assistant.execution.manager import AgentManager


class RuntimeOwnershipTest(unittest.IsolatedAsyncioTestCase):
    def manager(self, create):
        return AgentManager(
            MagicMock(
                delete_thread=AsyncMock(), list_threads=AsyncMock(return_value=[])
            ),
            MagicMock(exists=AsyncMock(return_value=False), save=AsyncMock()),
            MagicMock(create=create),
        )

    async def test_each_run_builds_new_runtime_and_only_registers_active_sessions(self):
        first, second = MagicMock(), MagicMock()
        create = AsyncMock(side_effect=[first, second])
        manager = self.manager(create)
        conversation = uuid4()
        for expected in (first, second):
            async with manager.use_runtime(1, conversation) as runtime:
                self.assertIs(runtime, expected)
                self.assertIs(
                    manager._active_sessions[(1, conversation)], runtime.session_service
                )
            self.assertFalse(manager._active_sessions)
        self.assertEqual(create.await_count, 2)

    async def test_different_conversations_register_and_release_independently(self):
        first, second = MagicMock(), MagicMock()
        manager = self.manager(AsyncMock(side_effect=[first, second]))
        first_id, second_id = uuid4(), uuid4()
        async with manager.use_runtime(1, first_id):
            async with manager.use_runtime(1, second_id):
                self.assertEqual(len(manager._active_sessions), 2)
            self.assertEqual(
                manager._active_sessions, {(1, first_id): first.session_service}
            )
        self.assertFalse(manager._active_sessions)

    async def test_run_cancellation_cancels_build_in_same_task(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def create(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        manager = self.manager(AsyncMock(side_effect=create))

        async def run():
            async with manager.use_runtime(1, uuid4()):
                self.fail("cancelled build must not enter execution")

        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())
        self.assertFalse(manager._active_sessions)

    async def test_cancellation_unregisters_sessions(self):
        manager = self.manager(AsyncMock(return_value=MagicMock()))
        entered = asyncio.Event()

        async def run():
            async with manager.use_runtime(1, uuid4()):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(manager._active_sessions)

    async def test_deleted_conversation_does_not_build(self):
        create = AsyncMock()
        manager = self.manager(create)
        manager._tombstones.exists.return_value = True
        with self.assertRaisesRegex(RuntimeError, "已被删除"):
            async with manager.use_runtime(1, uuid4()):
                self.fail("deleted conversation entered execution")
        create.assert_not_awaited()
