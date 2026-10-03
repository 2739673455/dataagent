"""共享执行上下文的操作租约、身份和取消传播。"""

import asyncio
from contextlib import contextmanager
from threading import Event
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.sandbox.execution import SandboxExecution
from app.sandbox.runtime import DockerRuntime
from tests.sandbox.fakes import FakeSandboxOwnership, build_sandbox_config


def _execution(ownership=None):
    runtime = MagicMock(spec=DockerRuntime)
    execution = SandboxExecution(
        7,
        uuid4(),
        100_001,
        build_sandbox_config(),
        ownership or FakeSandboxOwnership(),
        runtime,
    )
    return execution, runtime


def test_nested_operations_reuse_container_and_release_after_failure():
    """嵌套文件操作共用容器，异常退出后释放租约及线程本地引用。"""
    active = []

    class Ownership(FakeSandboxOwnership):
        @contextmanager
        def operation(self, user_id, conversation_id):
            active.append((user_id, conversation_id))
            try:
                yield
            finally:
                active.pop()

    execution, runtime = _execution(Ownership())
    container = runtime.get_running.return_value
    with pytest.raises(RuntimeError, match="文件失败"), execution.operation():
        assert execution.container is container
        with execution.operation():
            assert execution.container is container
            assert len(active) == 2
            raise RuntimeError("文件失败")
    assert active == []
    runtime.get_running.assert_called_once_with(7, None)
    with pytest.raises(RuntimeError, match="仅在操作期间"):
        _ = execution.container
    assert runtime.touch.call_count == 2


def test_async_cancellation_reaches_container_wait_and_releases_lease():
    """取消异步等待时通知容器获取线程，并最终释放工作区操作租约。"""
    entered, released = Event(), Event()

    class Ownership(FakeSandboxOwnership):
        @contextmanager
        def operation(self, user_id, conversation_id):
            try:
                yield
            finally:
                released.set()

    execution, runtime = _execution(Ownership())

    def get_running(user_id, cancel_event):
        entered.set()
        assert cancel_event.wait(5), "取消信号没有传到容器获取线程"
        raise asyncio.CancelledError

    runtime.get_running.side_effect = get_running

    def execute():
        with execution.operation():
            pytest.fail("已取消的操作不应执行命令")

    async def run():
        task = asyncio.create_task(execution.run_async(execute))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
        finally:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(released.wait, 5)

    asyncio.run(run())
    runtime.touch.assert_called_once_with(7)
