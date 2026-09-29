"""多进程 Planner 互斥与删除墓碑。"""

import asyncio
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.assistant.execution.manager import AgentManager
from app.assistant.execution.run import ConversationRunService
from app.assistant.execution.types import conversation_lifecycle_lock_name

_CONVERSATION_ID = uuid4()


class _DistributedLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[None]:
        lock = self._locks.setdefault(name, asyncio.Lock())
        if lock.locked():
            raise RuntimeError(f"lock busy: {name}")
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()


class PlannerIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_rejects_same_planner_across_workers(self) -> None:
        locks = _DistributedLockRegistry()
        provider = MagicMock(advisory_lock=locks.acquire)
        release = asyncio.Event()

        @asynccontextmanager
        async def use_runtime(*args):
            yield runtime

        async def stream(**kwargs):
            await release.wait()
            if False:
                yield {}

        runtime = MagicMock()
        runtime.planner.astream = stream
        first = ConversationRunService(
            MagicMock(use_runtime=use_runtime), MagicMock(), provider
        )
        second = ConversationRunService(
            MagicMock(use_runtime=use_runtime), MagicMock(), provider
        )
        try:
            events = await first.start(12, _CONVERSATION_ID, None, prepare=AsyncMock())
            with self.assertRaises(RuntimeError):
                await second.start(12, _CONVERSATION_ID, None, prepare=AsyncMock())
            self.assertTrue(await first.is_running(12, _CONVERSATION_ID))
            release.set()
            self.assertEqual([e.type async for e in events], ["done"])
        finally:
            await first.close()
            await second.close()

    async def test_persisted_tombstone_blocks_other_worker_execution(self) -> None:
        distributed_locks = _DistributedLockRegistry()
        tombstone = False

        tombstones = MagicMock()

        async def write_tombstone(*args: object, **kwargs: object) -> None:
            nonlocal tombstone
            del args, kwargs
            tombstone = True

        tombstones.save = AsyncMock(side_effect=write_tombstone)
        tombstones.exists = AsyncMock(side_effect=lambda *_: tombstone)
        persistence = MagicMock()
        persistence.delete_thread = AsyncMock()
        persistence.advisory_lock = lambda *args, **kwargs: distributed_locks.acquire(
            "conversation"
        )
        deleting_worker = AgentManager(persistence, tombstones, MagicMock())
        serving_worker = AgentManager(MagicMock(), tombstones, MagicMock())

        async with persistence.advisory_lock(
            conversation_lifecycle_lock_name(12, _CONVERSATION_ID)
        ):
            await deleting_worker.delete_agent_under_lifecycle_lock(
                12, _CONVERSATION_ID
            )

        with self.assertRaisesRegex(RuntimeError, "已被删除"):
            async with serving_worker.use_runtime(12, _CONVERSATION_ID):
                self.fail("deleted conversation entered execution")
        persistence.delete_thread.assert_awaited_once()
