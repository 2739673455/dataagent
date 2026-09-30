"""独立业务锁、原生线程删除和 namespace 清理的边界。"""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.assistant.checkpoints import postgres as store_module
from app.assistant.checkpoints.postgres import PostgresCheckpointStore
from app.assistant.execution.manager import AgentManager
from app.assistant.execution.types import get_thread_id
from app.shared.clients import postgres_advisory_locks as locks_module
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
from app.shared.config.app_config import cfg
from app.shared.errors.infrastructure import AdvisoryLockBusyError


@pytest.mark.parametrize("exit_error", [None, RuntimeError, asyncio.CancelledError])
def test_advisory_lock_uses_same_connection_and_releases_after_exit(exit_error):
    events = []
    connection = MagicMock()
    cursor = MagicMock(fetchone=AsyncMock(return_value={"acquired": True}))

    async def execute(statement, params):
        events.append((statement, params))
        return cursor

    connection.execute = AsyncMock(side_effect=execute)

    @asynccontextmanager
    async def borrow():
        events.append("borrow")
        try:
            yield connection
        finally:
            events.append("return")

    pool = MagicMock(connection=borrow, close=AsyncMock())
    factory = MagicMock(return_value=pool)
    factory.__getitem__.return_value = factory

    async def run():
        with patch.object(locks_module, "AsyncConnectionPool", factory):
            locks = PostgresAdvisoryLocks(cfg.langgraph_postgresql)
        name = "specialist:user_12:conversation_test:subagents/analysis/analyst/session"
        expected_key = int.from_bytes(
            hashlib.sha256(name.encode()).digest()[:8], "big", signed=True
        )

        async def use_lock():
            async with locks.advisory_lock(name):
                # 同进程重入也不能借另一条连接获得同名锁。
                with pytest.raises(AdvisoryLockBusyError):
                    async with locks.advisory_lock(name):
                        pytest.fail("同名锁不应重入")
                if exit_error is not None:
                    raise exit_error()

        if exit_error is None:
            await use_lock()
        else:
            with pytest.raises(exit_error):
                await use_lock()
        assert events == [
            "borrow",
            ("SELECT pg_try_advisory_lock(%s) AS acquired", (expected_key,)),
            ("SELECT pg_advisory_unlock(%s)", (expected_key,)),
            "return",
        ]
        events.clear()
        async with locks.advisory_lock(name):
            pass
        assert len(events) == 4
        await locks.close()

    asyncio.run(run())


def test_database_lock_conflict_does_not_unlock_someone_elses_lock():
    connection = MagicMock(
        execute=AsyncMock(
            return_value=MagicMock(fetchone=AsyncMock(return_value={"acquired": False}))
        )
    )
    pool = MagicMock(close=AsyncMock())
    pool.connection.return_value.__aenter__.return_value = connection
    factory = MagicMock(return_value=pool)
    factory.__getitem__.return_value = factory

    async def run():
        with patch.object(locks_module, "AsyncConnectionPool", factory):
            locks = PostgresAdvisoryLocks(cfg.langgraph_postgresql)
        for _ in range(2):
            with pytest.raises(AdvisoryLockBusyError):
                async with locks.advisory_lock("conversation:test"):
                    pytest.fail("数据库锁冲突")
        assert connection.execute.await_count == 2
        assert all(
            "pg_try_advisory_lock" in call.args[0]
            for call in connection.execute.await_args_list
        )
        await locks.close()

    asyncio.run(run())


def test_thread_deletion_uses_native_saver_inside_lifecycle_lock():
    events = []
    conversation_id = uuid4()

    @asynccontextmanager
    async def lock(name):
        events.append(("lock", name))
        try:
            yield
        finally:
            events.append("unlock")

    async def delete(thread_id):
        assert events[-1] == "tombstone"
        events.append(("delete", thread_id))

    store = MagicMock(
        checkpointer=MagicMock(adelete_thread=AsyncMock(side_effect=delete))
    )
    tombstones = MagicMock(
        save=AsyncMock(side_effect=lambda *_: events.append("tombstone"))
    )

    async def run():
        manager = AgentManager(store, tombstones, MagicMock(advisory_lock=lock))
        await manager.delete_agent(12, conversation_id)
        await manager.close()

    asyncio.run(run())
    thread_id = get_thread_id(12, conversation_id)
    assert events == [
        ("lock", f"conversation:{thread_id}"),
        "tombstone",
        ("delete", thread_id),
        "unlock",
    ]


def test_namespace_delete_is_atomic_and_user_cleanup_calls_native_saver():
    connection = MagicMock(execute=AsyncMock())
    pool = MagicMock(close=AsyncMock())
    pool.connection.return_value.__aenter__.return_value = connection
    factory = MagicMock(return_value=pool)
    factory.__getitem__.return_value = factory
    saver = MagicMock(adelete_thread=AsyncMock())

    async def run():
        with (
            patch.object(store_module, "AsyncConnectionPool", factory),
            patch.object(store_module, "AsyncPostgresSaver", return_value=saver),
        ):
            store = PostgresCheckpointStore(cfg.langgraph_postgresql)
        connection.execute.return_value = MagicMock(rowcount=1)
        assert await store.delete_checkpoint_namespace(
            "thread", "subagents/a/analyst/b"
        )
        assert len(connection.execute.await_args_list) == 3
        assert all(
            call.args[1] == ("thread", "subagents/a/analyst/b")
            for call in connection.execute.await_args_list
        )
        connection.transaction.return_value.__aexit__.assert_awaited_once_with(
            None, None, None
        )
        connection.execute.reset_mock()
        connection.execute.return_value = MagicMock(
            fetchall=AsyncMock(
                return_value=[
                    {"thread_id": "user_12:conversation_a"},
                    {"thread_id": "user_12:conversation_b"},
                ]
            )
        )
        await store.delete_user_threads(12)
        assert connection.execute.call_args.args[1] == (
            "user_12:conversation_",
            "user_12:conversation_",
        )
        assert [call.args[0] for call in saver.adelete_thread.await_args_list] == [
            "user_12:conversation_a",
            "user_12:conversation_b",
        ]
        await store.close()

    asyncio.run(run())
