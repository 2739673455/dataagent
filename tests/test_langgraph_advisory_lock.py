"""PostgreSQL advisory lock 的连接池竞争单元测试"""

from __future__ import annotations

import asyncio
import unittest
from types import TracebackType
from typing import Any, cast
from unittest.mock import MagicMock

from app.clients.langgraph_postgres_manager import LangGraphPostgresManager


class _FakeCursor:
    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired

    async def fetchone(self) -> dict[str, bool]:
        return {"acquired": self._acquired}


class _FakeConnection:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool
        self._held_key: int | None = None

    async def execute(
        self,
        query: str,
        params: tuple[int],
    ) -> _FakeCursor:
        lock_key = params[0]
        if "pg_try_advisory_lock" in query:
            acquired = lock_key not in self._pool.held_keys
            if acquired:
                self._pool.held_keys.add(lock_key)
                self._held_key = lock_key
            return _FakeCursor(acquired)
        if "pg_advisory_unlock" in query:
            self._pool.held_keys.discard(lock_key)
            self._held_key = None
            return _FakeCursor(True)
        raise AssertionError(query)

    def release_held_lock(self) -> None:
        if self._held_key is not None:
            self._pool.held_keys.discard(self._held_key)
            self._held_key = None


class _FakeConnectionContext:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool
        self._connection = _FakeConnection(pool)

    async def __aenter__(self) -> _FakeConnection:
        await self._pool.capacity.acquire()
        self._pool.borrowed += 1
        self._pool.max_borrowed = max(
            self._pool.max_borrowed,
            self._pool.borrowed,
        )
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self._connection.release_held_lock()
        self._pool.borrowed -= 1
        self._pool.capacity.release()


class _FakePool:
    def __init__(self, size: int) -> None:
        self.capacity = asyncio.Semaphore(size)
        self.held_keys: set[int] = set()
        self.borrowed = 0
        self.max_borrowed = 0

    def connection(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self)

    async def probe(self) -> None:
        async with self.connection():
            await asyncio.sleep(0)


class AdvisoryLockTest(unittest.IsolatedAsyncioTestCase):
    """验证失败等待者及时归还小连接池连接"""

    async def test_cross_worker_waiter_does_not_starve_pool(self) -> None:
        pool = _FakePool(size=2)
        first_manager = LangGraphPostgresManager(MagicMock())
        second_manager = LangGraphPostgresManager(MagicMock())
        cast(Any, first_manager)._advisory_pool = pool
        cast(Any, second_manager)._advisory_pool = pool
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()
        waiter_entered = asyncio.Event()

        async def hold_lock() -> None:
            async with first_manager.advisory_lock("shared", timeout=1):
                holder_entered.set()
                await release_holder.wait()

        async def wait_for_lock() -> None:
            async with second_manager.advisory_lock("shared", timeout=1):
                waiter_entered.set()

        holder = asyncio.create_task(hold_lock())
        await holder_entered.wait()
        waiter = asyncio.create_task(wait_for_lock())
        await asyncio.sleep(0.01)

        await asyncio.wait_for(pool.probe(), timeout=0.2)
        self.assertFalse(waiter_entered.is_set())

        release_holder.set()
        await asyncio.gather(holder, waiter)
        self.assertTrue(waiter_entered.is_set())
        self.assertLessEqual(pool.max_borrowed, 2)

    async def test_same_process_waiters_use_local_lock(self) -> None:
        pool = _FakePool(size=2)
        manager = LangGraphPostgresManager(MagicMock())
        cast(Any, manager)._advisory_pool = pool
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()

        async def hold_lock() -> None:
            async with manager.advisory_lock("shared", timeout=1):
                holder_entered.set()
                await release_holder.wait()

        async def wait_for_lock() -> None:
            async with manager.advisory_lock("shared", timeout=1):
                return

        holder = asyncio.create_task(hold_lock())
        await holder_entered.wait()
        waiters = [asyncio.create_task(wait_for_lock()) for _ in range(20)]
        await asyncio.sleep(0.01)

        self.assertEqual(pool.borrowed, 1)
        await asyncio.wait_for(pool.probe(), timeout=0.2)

        release_holder.set()
        await asyncio.gather(holder, *waiters)

    async def test_successful_holders_do_not_use_checkpoint_pool(self) -> None:
        advisory_pool = _FakePool(size=12)
        checkpoint_pool = _FakePool(size=1)
        manager = LangGraphPostgresManager(MagicMock())
        cast(Any, manager)._advisory_pool = advisory_pool
        cast(Any, manager)._pool = checkpoint_pool
        entered = [asyncio.Event() for _ in range(9)]
        release = asyncio.Event()

        async def hold_lock(index: int) -> None:
            async with manager.advisory_lock(f"lock-{index}", timeout=1):
                entered[index].set()
                await release.wait()

        holders = [asyncio.create_task(hold_lock(index)) for index in range(9)]
        await asyncio.gather(*(event.wait() for event in entered))

        await asyncio.wait_for(checkpoint_pool.probe(), timeout=0.2)
        self.assertEqual(checkpoint_pool.max_borrowed, 1)
        self.assertEqual(advisory_pool.borrowed, 9)

        release.set()
        await asyncio.gather(*holders)


if __name__ == "__main__":
    unittest.main()
