"""前台 Shell 输出、文件保留和取消传播。"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.assistant.agents.tools.shell import create_shell_tool
from app.sandbox import DockerSandboxBackend
from app.sandbox.shell_runner import DockerShellJobRunner, ShellResult
from tests.sandbox.fakes import FakeSandboxOwnership


def _backend(runner):
    backend = DockerSandboxBackend(
        1, uuid4(), 10001, FakeSandboxOwnership(), lambda: None, MagicMock()
    )
    backend._shell_jobs = runner
    return backend


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_shell_waits_for_completion_and_returns_output():
    started, finish = asyncio.Event(), asyncio.Event()

    async def run(job_id, command):
        started.set()
        await finish.wait()
        return ShellResult(status="completed", output="done", exit_code=0)

    runner = MagicMock(
        workspace_dir="/data/work",
        arun=AsyncMock(side_effect=run),
        acleanup=AsyncMock(),
    )
    tool = create_shell_tool(_backend(runner))
    assert tool.name == "shell"
    task = asyncio.create_task(tool.ainvoke({"command": "sleep 1"}))
    await started.wait()
    assert not task.done()
    finish.set()
    assert await task == "done"
    runner.acleanup.assert_awaited_once_with(
        runner.arun.call_args.args[0], remove_log=True
    )


@pytest.mark.anyio
async def test_truncated_output_keeps_log_and_returns_path():
    runner = MagicMock(
        workspace_dir="/data/work",
        arun=AsyncMock(
            return_value=ShellResult(
                status="completed", output="head...tail", output_inline_truncated=True
            )
        ),
        acleanup=AsyncMock(),
    )
    backend = _backend(runner)
    output = await create_shell_tool(backend).ainvoke({"command": "large-output"})
    job_id = runner.arun.call_args.args[0]
    assert (
        output
        == f"head...tail\n详细输出文件: {backend.workspace_dir}/large_tool_results/shell_jobs/{job_id}.log"
    )
    runner.acleanup.assert_awaited_once_with(job_id, remove_log=False)


@pytest.mark.anyio
async def test_nonzero_exit_is_reported_with_output():
    runner = MagicMock(
        arun=AsyncMock(
            return_value=ShellResult(status="failed", output="bad", exit_code=2)
        ),
        acleanup=AsyncMock(),
    )
    assert (
        await create_shell_tool(_backend(runner)).ainvoke({"command": "exit 2"})
        == "bad\nShell 命令以退出码 2 结束"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("queued", [False, True])
async def test_runner_cancellation_waits_for_thread_and_stops_started_process(queued):
    backend = MagicMock(_operation_local=threading.local())
    runner = DockerShellJobRunner(backend)
    entered = threading.Event()
    exited = threading.Event()
    released = threading.Event()

    def run(job_id, command, started_callback):
        entered.set()
        if queued:
            backend._operation_local.cancel_event.wait(2)
        else:
            started_callback()
            released.wait(2)
        exited.set()
        return ShellResult(status="completed")

    async def cancel(job_id):
        released.set()

    runner.run = MagicMock(side_effect=run)
    runner.acancel = AsyncMock(side_effect=cancel)
    async with asyncio.timeout(3):
        task = asyncio.create_task(runner.arun("job_12345678", "sleep 100"))
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set()
    if queued:
        runner.acancel.assert_not_awaited()
    else:
        runner.acancel.assert_awaited_once_with("job_12345678")
