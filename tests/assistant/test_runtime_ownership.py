"""运行时构建、借用、淘汰和删除的交错测试。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.assistant.execution.manager import AgentManager


def _runtime():
    return MagicMock(shell_jobs=MagicMock(cleanup=AsyncMock()))


class RuntimeOwnershipTest(unittest.IsolatedAsyncioTestCase):
    def manager(self, create):
        manager = AgentManager(
            MagicMock(delete_thread=AsyncMock()),
            MagicMock(exists=AsyncMock(return_value=False), save=AsyncMock()),
            MagicMock(init=AsyncMock(), create=create, close=AsyncMock()),
            max_cached_runtimes=1,
        )
        self.addAsyncCleanup(manager.close)
        return manager

    async def test_borrowed_runtime_survives_eviction_until_released(self):
        first, second, third = _runtime(), _runtime(), _runtime()
        manager = self.manager(AsyncMock(side_effect=[first, second, third]))
        first_id, second_id = uuid4(), uuid4()
        async with manager.use_runtime(1, first_id) as borrowed:
            self.assertIs(borrowed, first)
            async with manager.use_runtime(1, second_id):
                first.shell_jobs.cleanup.assert_not_awaited()
            async with manager.use_runtime(1, first_id) as reused:
                self.assertIs(reused, first)
        async with manager.use_runtime(1, uuid4()):
            first.shell_jobs.cleanup.assert_awaited_once()
            second.shell_jobs.cleanup.assert_awaited_once()

    async def test_cancelled_waiter_does_not_cancel_shared_build(self):
        entered, release = asyncio.Event(), asyncio.Event()
        runtime = _runtime()

        async def create(*args):
            entered.set()
            await release.wait()
            return runtime

        factory = AsyncMock(side_effect=create)
        manager = self.manager(factory)
        conversation_id = uuid4()

        async def borrow():
            async with manager.use_runtime(1, conversation_id) as result:
                return result

        async with asyncio.timeout(1):
            first = asyncio.create_task(borrow())
            await entered.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(borrow())
            release.set()
            self.assertIs(await second, runtime)
        factory.assert_awaited_once()
        self.assertEqual(manager._runtime_users, {})

    async def test_deletion_discards_late_result_even_if_builder_swallows_cancel(self):
        entered = asyncio.Event()
        runtime = _runtime()

        async def create(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return runtime

        manager = self.manager(AsyncMock(side_effect=create))
        conversation_id = uuid4()

        async def borrow():
            async with manager.use_runtime(1, conversation_id):
                self.fail("deleted runtime must never be borrowed")

        async with asyncio.timeout(1):
            waiting = asyncio.create_task(borrow())
            await entered.wait()
            await manager.delete_agent_under_lifecycle_lock(1, conversation_id)
            with self.assertRaisesRegex(RuntimeError, "构建已失效"):
                await waiting
        runtime.shell_jobs.cleanup.assert_awaited_once()
        self.assertEqual(manager._conversation_runtimes, {})
        self.assertEqual(manager._runtime_users, {})
        with self.assertRaisesRegex(RuntimeError, "已被删除"):
            async with manager.use_runtime(1, conversation_id):
                self.fail("deleted runtime must never be rebuilt")
