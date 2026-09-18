"""事件循环入口和 PostgreSQL 资源异常路径回归测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.shared import async_runtime
from app.shared.clients import langgraph_postgres_manager as persistence_module
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
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


@pytest.mark.parametrize("failure_stage", ["open", "setup", "cancel"])
def test_persistence_init_rolls_back_both_pools(failure_stage: str) -> None:
    pools = [MagicMock(), MagicMock()]
    for pool in pools:
        pool.open = AsyncMock()
        pool.close = AsyncMock()
    saver = MagicMock()
    saver.setup = AsyncMock()
    error = (
        asyncio.CancelledError() if failure_stage == "cancel" else RuntimeError("init")
    )
    if failure_stage == "open":
        pools[1].open.side_effect = error
    else:
        saver.setup.side_effect = error
    factory = MagicMock(side_effect=pools)
    factory.__getitem__.return_value = factory
    manager = LangGraphPostgresManager(cfg.langgraph_postgresql)

    async def run() -> None:
        with pytest.raises(type(error)):
            await manager.init()
        for pool in pools:
            pool.close.assert_awaited_once()
        with pytest.raises(RuntimeError, match="尚未初始化"):
            manager.get_checkpointer()
        await manager.close()

    with (
        patch.object(persistence_module, "AsyncConnectionPool", factory),
        patch.object(persistence_module, "AsyncPostgresSaver", return_value=saver),
    ):
        asyncio.run(run())


def test_persistence_close_attempts_other_pool_after_failure() -> None:
    manager = LangGraphPostgresManager(cfg.langgraph_postgresql)
    pool, advisory = MagicMock(), MagicMock()
    pool.close = AsyncMock()
    advisory.close = AsyncMock(side_effect=RuntimeError("close"))
    manager._pool = pool
    manager._advisory_pool = advisory

    async def run() -> None:
        with pytest.raises(RuntimeError, match="close"):
            await manager.close()
        pool.close.assert_awaited_once()
        advisory.close.assert_awaited_once()
        await manager.close()

    asyncio.run(run())
