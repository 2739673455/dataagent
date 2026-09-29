"""每次运行独立构图，准备资源时取消向下传播。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.assistant.agents.manager import AgentManager


class RuntimeOwnershipTest(unittest.IsolatedAsyncioTestCase):
    def manager(self, create):
        manager = AgentManager(
            MagicMock(adelete_thread=AsyncMock()),
            MagicMock(get_backend=create),
            MagicMock(),
            MagicMock(),
        )
        manager._build_planner = lambda user_id, conversation_id, backend: backend
        return manager

    async def test_each_run_builds_new_runtime(self):
        first, second = MagicMock(), MagicMock()
        create = AsyncMock(side_effect=[first, second])
        manager = self.manager(create)
        conversation = uuid4()
        for expected in (first, second):
            graph = await manager.create_planner(1, conversation)
            self.assertIs(graph, expected)
        self.assertEqual(create.await_count, 2)

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
            await manager.create_planner(1, uuid4())
            self.fail("cancelled build must not enter execution")

        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())

    async def test_cancellation_propagates_to_execution(self):
        manager = self.manager(AsyncMock(return_value=MagicMock()))
        entered = asyncio.Event()

        async def run():
            await manager.create_planner(1, uuid4())
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
