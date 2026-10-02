"""事件循环入口和 PostgreSQL 资源异常路径回归测试。"""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.assistant.repositories import checkpoint as persistence_module
from app.assistant.repositories.checkpoint import PostgresCheckpointStore
from app.shared import async_runtime
from app.shared.clients import postgres_advisory_locks as locks_module
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
from app.shared.config.app_config import cfg


def test_runner_uses_selector_on_windows_and_closes_loop() -> None:
    loops = []
    selector = asyncio.SelectorEventLoop

    async def operation() -> int:
        loops.append(asyncio.get_running_loop())
        return 7

    with (
        patch.object(async_runtime.sys, "platform", "win32"),
        patch.object(
            async_runtime.asyncio, "SelectorEventLoop", wraps=selector
        ) as factory,
    ):
        assert async_runtime.run_async(operation()) == 7
        assert async_runtime.run_async(operation()) == 7
    assert factory.call_count == 2
    assert loops[0] is not loops[1]
    assert all(loop.is_closed() for loop in loops)


@pytest.mark.parametrize("failure_stage", ["open", "setup", "cancel", "lock_open"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_persistence_scope_releases_both_pools(failure_stage, close_fails):
    pools = [MagicMock(open=AsyncMock(), close=AsyncMock()) for _ in range(2)]
    saver = MagicMock(setup=AsyncMock())
    error = (
        asyncio.CancelledError() if failure_stage == "cancel" else RuntimeError("init")
    )
    if failure_stage == "open":
        pools[0].open.side_effect = error
    elif failure_stage == "lock_open":
        pools[1].open.side_effect = error
    else:
        saver.setup.side_effect = error
    if close_fails:
        pools[1].close.side_effect = RuntimeError("close")
    factories = [MagicMock(return_value=pool) for pool in pools]
    for factory in factories:
        factory.__getitem__.return_value = factory

    async def run():
        with pytest.raises(RuntimeError if close_fails else type(error)):
            async with AsyncExitStack() as stack:
                store = PostgresCheckpointStore(cfg.langgraph_postgresql)
                stack.push_async_callback(store.close)
                locks = PostgresAdvisoryLocks(cfg.langgraph_postgresql)
                stack.push_async_callback(locks.close)
                assert store.checkpointer is saver
                await store.init()
                await locks.init()
        for pool in pools:
            pool.close.assert_awaited_once()
        saver_factory.assert_called_once_with(pools[0])
        assert factories[0].call_args.kwargs["max_size"] == 20
        assert factories[1].call_args.kwargs["max_size"] == 12

    with (
        patch.object(persistence_module, "AsyncConnectionPool", factories[0]),
        patch.object(locks_module, "AsyncConnectionPool", factories[1]),
        patch.object(
            persistence_module, "AsyncPostgresSaver", return_value=saver
        ) as saver_factory,
    ):
        asyncio.run(run())
