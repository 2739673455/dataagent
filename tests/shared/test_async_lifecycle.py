"""事件循环入口和 PostgreSQL 资源异常路径回归测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.shared import async_runtime


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


@pytest.mark.parametrize("failure_stage", ["open", "setup", "cancel", "close"])
def test_lifespan_closes_checkpoint_pool_on_failure(failure_stage: str) -> None:
    from app import runtime

    checkpoint_pool = MagicMock(open=AsyncMock(), close=AsyncMock())
    saver = MagicMock(setup=AsyncMock())
    error = (
        asyncio.CancelledError()
        if failure_stage == "cancel"
        else RuntimeError("failure")
    )
    if failure_stage == "open":
        checkpoint_pool.open.side_effect = error
    elif failure_stage == "close":
        checkpoint_pool.close.side_effect = error
    else:
        saver.setup.side_effect = error
    resources = MagicMock(
        checkpoint_pool=checkpoint_pool,
        checkpointer=saver,
    )
    for name in (
        "query_clients",
        "admin_doris",
        "auth",
        "meta",
        "assistant",
        "es",
        "embedding",
        "sandbox",
        "agents",
        "runs",
        "tasks",
    ):
        setattr(
            resources,
            name,
            MagicMock(close=AsyncMock(), init=AsyncMock(), init_tables=AsyncMock()),
        )

    async def run() -> None:
        with pytest.raises(type(error)):
            async with runtime.lifespan(MagicMock()):
                assert failure_stage == "close"
        checkpoint_pool.close.assert_awaited_once()

    def create(stack):
        for name in (
            "query_clients",
            "admin_doris",
            "auth",
            "meta",
            "assistant",
            "es",
            "embedding",
            "checkpoint_pool",
            "sandbox",
            "agents",
            "runs",
            "tasks",
        ):
            stack.push_async_callback(getattr(resources, name).close)
        return resources

    with patch.object(runtime, "_create_resources", side_effect=create):
        asyncio.run(run())
