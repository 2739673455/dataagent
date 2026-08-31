"""Specialist Shell Job Runtime 与模型上下文测试"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, cast
from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage

from app.assistant.agents.shell_jobs import (
    ShellJobContextMiddleware,
    ShellJobError,
    ShellJobResult,
    ShellJobRuntime,
)
from app.sandbox.backend import (
    DockerSandboxBackend,
    SandboxShellJobCancellation,
    SandboxShellJobExecution,
)


class _FakeShellBackend:
    def __init__(
        self,
        result: SandboxShellJobExecution | None = None,
        *,
        blocked: bool = False,
    ) -> None:
        self.result = result or SandboxShellJobExecution(
            status="completed",
            exit_code=0,
            output="done\n",
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.cancelled: list[str] = []
        self.cleaned: list[str] = []

    async def arun_shell_job(
        self,
        job_id: str,
        command: str,
        started_callback: Any = None,
    ) -> SandboxShellJobExecution:
        del command
        self.started.set()
        if started_callback is not None:
            started_callback()
        await self.release.wait()
        return self.result

    async def acancel_shell_job(self, job_id: str) -> SandboxShellJobCancellation:
        self.cancelled.append(job_id)
        self.result = SandboxShellJobExecution(status="failed", exit_code=-15)
        self.release.set()
        return SandboxShellJobCancellation(
            ready=True,
            signal_sent=True,
            exited=True,
        )

    async def acleanup_shell_job_control(self, job_id: str) -> None:
        self.cleaned.append(job_id)


def _runtime(
    backend: _FakeShellBackend,
    *,
    foreground_wait_seconds: float = 0.01,
) -> ShellJobRuntime:
    return ShellJobRuntime(
        cast(DockerSandboxBackend, backend),
        foreground_wait_seconds=foreground_wait_seconds,
    )


class ShellJobRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_foreground_completion_is_reviewed_and_keeps_small_output(
        self,
    ) -> None:
        backend = _FakeShellBackend()
        runtime = _runtime(backend)

        result = await runtime.execute("printf done")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, "done\n")
        self.assertIsNotNone(result.reviewed_at)
        self.assertEqual(runtime.list(), [])
        self.assertEqual(len(runtime.list(include_reviewed=True)), 1)
        await runtime.cleanup()

    async def test_background_completion_stays_unreviewed_until_get(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)

        running = await runtime.execute("long command")
        self.assertEqual(running.status, "running")
        self.assertIsNone(running.reviewed_at)

        backend.release.set()
        job = await runtime.get(running.job_id, wait_seconds=1)
        self.assertIsInstance(job, ShellJobResult)
        assert isinstance(job, ShellJobResult)
        self.assertEqual(job.status, "completed")
        self.assertIsNotNone(job.reviewed_at)
        self.assertIsNone(job.output)
        self.assertEqual(runtime.model_context()["finished_unreviewed"], [])
        await runtime.cleanup()

    async def test_list_and_model_context_do_not_review_finished_job(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)
        running = await runtime.execute("long command")
        backend.release.set()
        for _ in range(100):
            if runtime.model_context()["finished_unreviewed"]:
                break
            await asyncio.sleep(0.01)

        listed = runtime.list()
        context = runtime.model_context()

        self.assertIsNone(listed[0].reviewed_at)
        self.assertEqual(context["finished_unreviewed"][0]["job_id"], running.job_id)
        self.assertNotIn("command", context["finished_unreviewed"][0])
        await runtime.cleanup()

    async def test_get_timeout_returns_running_and_unknown_job_is_explicit(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)
        running = await runtime.execute("long command")

        current = await runtime.get(running.job_id, wait_seconds=0.01)
        missing = await runtime.get("job_deadbeef")

        self.assertIsInstance(current, ShellJobResult)
        self.assertIsInstance(missing, ShellJobError)
        assert isinstance(missing, ShellJobError)
        self.assertEqual(current.status, "running")
        self.assertEqual(missing.status, "error")
        self.assertEqual(missing.code, "job_not_found")
        await runtime.cleanup()

    async def test_cancel_marks_confirmed_process_group_exit_as_cancelled(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)
        running = await runtime.execute("long command")

        cancelled = await runtime.cancel(running.job_id)

        self.assertIsInstance(cancelled, ShellJobResult)
        assert isinstance(cancelled, ShellJobResult)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNotNone(cancelled.reviewed_at)
        self.assertEqual(backend.cancelled, [running.job_id])
        await runtime.cleanup()

    async def test_cancelled_tool_wait_does_not_cancel_monitor(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend, foreground_wait_seconds=10)
        execute_task = asyncio.create_task(runtime.execute("long command"))
        await backend.started.wait()
        execute_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execute_task

        await runtime.cleanup()

        self.assertEqual(len(backend.cancelled), 1)
        self.assertEqual(len(backend.cleaned), 1)


class ShellJobContextMiddlewareTest(unittest.TestCase):
    @staticmethod
    def _request() -> ModelRequest[Any]:
        return ModelRequest(
            model=MagicMock(),
            messages=[],
            system_message=SystemMessage(content="base prompt"),
            tools=[],
        )

    def test_empty_context_keeps_original_request(self) -> None:
        runtime = MagicMock()
        runtime.model_context.return_value = {
            "running": [],
            "finished_unreviewed": [],
        }
        request = self._request()
        captured: list[ModelRequest[Any]] = []

        def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(projected)
            return ModelResponse(result=[AIMessage(content="ok")])

        ShellJobContextMiddleware(cast(Any, runtime)).wrap_model_call(request, handler)

        self.assertIs(captured[0], request)

    def test_context_is_only_appended_to_request_copy_without_output(self) -> None:
        runtime = MagicMock()
        runtime.model_context.return_value = {
            "running": [
                {
                    "job_id": "job_12345678",
                    "status": "running",
                    "output_path": "large_tool_results/shell_jobs/job_12345678.log",
                }
            ],
            "finished_unreviewed": [],
        }
        request = self._request()
        captured: list[ModelRequest[Any]] = []

        def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(projected)
            return ModelResponse(result=[AIMessage(content="ok")])

        ShellJobContextMiddleware(cast(Any, runtime)).wrap_model_call(request, handler)

        self.assertIsNot(captured[0], request)
        assert request.system_message is not None
        assert captured[0].system_message is not None
        self.assertEqual(request.system_message.text, "base prompt")
        projected_prompt = captured[0].system_message.text
        self.assertIn("<shell_jobs>", projected_prompt)
        self.assertIn("job_12345678", projected_prompt)
        self.assertNotIn('"output"', projected_prompt)


if __name__ == "__main__":
    unittest.main()
