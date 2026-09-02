"""Specialist Shell Job Runtime 与模型上下文测试。"""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph.message import add_messages

from app.assistant.agents.middleware.user_message_context import (
    SHELL_JOB_CONTEXT_KEY,
    UserMessageContextMiddleware,
)
from app.assistant.agents.shell_jobs import (
    ShellJobError,
    ShellJobResult,
    ShellJobRuntime,
    ShellJobSummary,
)
from app.assistant.agents.tools.shell import create_shell_tools
from app.sandbox.shell_runner import (
    DockerShellJobRunner,
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
        self.workspace_dir = "/data/conversation/sessions/analysis/analyst/session"
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
        self.cleaned: list[tuple[str, bool]] = []

    async def arun(
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

    async def acancel(self, job_id: str) -> SandboxShellJobCancellation:
        self.cancelled.append(job_id)
        self.result = SandboxShellJobExecution(status="failed", exit_code=-15)
        self.release.set()
        return SandboxShellJobCancellation(
            ready=True,
            signal_sent=True,
            exited=True,
        )

    async def acleanup(self, job_id: str, *, remove_log: bool = False) -> None:
        self.cleaned.append((job_id, remove_log))


def _runtime(
    backend: _FakeShellBackend,
    *,
    foreground_wait_seconds: float = 0.01,
) -> ShellJobRuntime:
    return ShellJobRuntime(
        cast(DockerShellJobRunner, backend),
        foreground_wait_seconds=foreground_wait_seconds,
    )


class ShellJobRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_foreground_completion_returns_only_output_and_is_not_public(
        self,
    ) -> None:
        backend = _FakeShellBackend()
        runtime = _runtime(backend)

        result = await runtime.start("printf done")

        self.assertEqual(result, "done\n")
        self.assertEqual(runtime.list(), [])
        self.assertEqual(len(backend.cleaned), 1)
        self.assertTrue(backend.cleaned[0][1])
        await runtime.cleanup()

    async def test_foreground_truncation_returns_output_path_and_keeps_log(
        self,
    ) -> None:
        backend = _FakeShellBackend(
            SandboxShellJobExecution(
                status="completed",
                exit_code=0,
                output="HEAD\n...[middle output truncated]...\nTAIL\n",
                output_inline_truncated=True,
            )
        )
        runtime = _runtime(backend)

        result = await runtime.start("large output")

        self.assertIsInstance(result, str)
        assert isinstance(result, str)
        self.assertIn("HEAD", result)
        self.assertIn("TAIL", result)
        self.assertIn("详细输出文件: ", result)
        self.assertEqual(runtime.list(), [])
        self.assertEqual(len(backend.cleaned), 1)
        self.assertFalse(backend.cleaned[0][1])
        await runtime.cleanup()

    async def test_foreground_failure_without_output_returns_error_text(self) -> None:
        backend = _FakeShellBackend(
            SandboxShellJobExecution(status="failed", exit_code=1)
        )
        runtime = _runtime(backend)

        result = await runtime.start("false")

        self.assertEqual(result, "Shell 命令以退出码 1 结束")
        await runtime.cleanup()

    async def test_background_terminal_result_is_consumed_by_get(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)

        running = await runtime.start("long command")
        self.assertIsInstance(running, ShellJobResult)
        assert isinstance(running, ShellJobResult)
        self.assertEqual(running.status, "running")
        self.assertEqual(len(runtime.list()), 1)

        backend.release.set()
        job = await runtime.get(running.job_id, wait_seconds=1)
        missing = await runtime.get(running.job_id)

        self.assertIsInstance(job, ShellJobResult)
        assert isinstance(job, ShellJobResult)
        self.assertEqual(job.status, "completed")
        self.assertIsNone(job.output)
        self.assertIsInstance(missing, ShellJobError)
        self.assertEqual(runtime.list(), [])
        await runtime.cleanup()

    async def test_list_does_not_consume_finished_background_job(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)
        running = await runtime.start("long command")
        assert isinstance(running, ShellJobResult)
        backend.release.set()
        for _ in range(100):
            listed = runtime.list()
            if listed and listed[0].status == "completed":
                break
            await asyncio.sleep(0.01)

        listed = runtime.list()
        result = await runtime.get(running.job_id)

        self.assertEqual(listed[0].job_id, running.job_id)
        self.assertIsInstance(result, ShellJobResult)
        await runtime.cleanup()

    async def test_get_running_job_can_be_polled_without_consuming_it(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)
        running = await runtime.start("long command")
        assert isinstance(running, ShellJobResult)

        first = await runtime.get(running.job_id, wait_seconds=0.01)
        second = await runtime.get(running.job_id)

        self.assertIsInstance(first, ShellJobResult)
        self.assertIsInstance(second, ShellJobResult)
        assert isinstance(first, ShellJobResult)
        assert isinstance(second, ShellJobResult)
        self.assertEqual(first.status, "running")
        self.assertEqual(second.status, "running")
        self.assertEqual(len(runtime.list()), 1)
        await runtime.cleanup()

    async def test_concurrent_get_consumes_terminal_result_once(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)
        running = await runtime.start("long command")
        assert isinstance(running, ShellJobResult)
        backend.release.set()

        first, second = await asyncio.gather(
            runtime.get(running.job_id, wait_seconds=1),
            runtime.get(running.job_id, wait_seconds=1),
        )

        results = [first, second]
        self.assertEqual(
            sum(isinstance(result, ShellJobResult) for result in results),
            1,
        )
        self.assertEqual(
            sum(isinstance(result, ShellJobError) for result in results),
            1,
        )
        await runtime.cleanup()

    async def test_cancel_consumes_confirmed_terminal_job(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend)
        running = await runtime.start("long command")
        assert isinstance(running, ShellJobResult)

        cancelled = await runtime.cancel(running.job_id)
        missing = await runtime.get(running.job_id)

        self.assertIsInstance(cancelled, ShellJobResult)
        assert isinstance(cancelled, ShellJobResult)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(backend.cancelled, [running.job_id])
        self.assertIsInstance(missing, ShellJobError)
        await runtime.cleanup()

    async def test_cancelled_foreground_wait_publishes_running_job(self) -> None:
        backend = _FakeShellBackend(blocked=True)
        runtime = _runtime(backend, foreground_wait_seconds=10)
        shell_task = asyncio.create_task(runtime.start("long command"))
        await backend.started.wait()
        shell_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await shell_task

        self.assertEqual(len(runtime.list()), 1)
        await runtime.cleanup()


class ShellJobToolSchemaTest(unittest.TestCase):
    def test_list_shell_jobs_does_not_accept_include_reviewed(self) -> None:
        runtime = _runtime(_FakeShellBackend())
        tools = {tool.name: tool for tool in create_shell_tools(runtime)}

        fields = cast(
            Any,
            tools["list_shell_jobs"].args_schema,
        ).model_fields

        self.assertNotIn("include_reviewed", fields)


class UserMessageShellJobContextTest(unittest.TestCase):
    @staticmethod
    def _middleware(runtime: Any) -> UserMessageContextMiddleware:
        return UserMessageContextMiddleware(
            cast(Any, MagicMock()),
            "/data/conversation",
            cast(Any, runtime),
        )

    @staticmethod
    def _request(message: HumanMessage | None = None) -> ModelRequest[Any]:
        return ModelRequest(
            model=MagicMock(),
            messages=[] if message is None else [message],
            system_message=SystemMessage(content="base prompt"),
            tools=[],
        )

    @staticmethod
    def _job(job_id: str = "job_12345678") -> ShellJobSummary:
        return ShellJobSummary(
            job_id=job_id,
            status="running",
            command="long command",
            started_at=datetime.now(UTC),
            elapsed_seconds=1.0,
            output_path=(
                "/data/conversation/sessions/analysis/analyst/session/"
                f"large_tool_results/shell_jobs/{job_id}.log"
            ),
        )

    def test_before_model_without_jobs_keeps_state(self) -> None:
        runtime = MagicMock()
        runtime.list.return_value = []
        state = {"messages": [HumanMessage(id="user-1", content="question")]}

        update = self._middleware(runtime).before_model(
            cast(Any, state),
            cast(Any, MagicMock()),
        )

        self.assertIsNone(update)

    def test_before_model_freezes_jobs_on_latest_real_user_message(self) -> None:
        runtime = MagicMock()
        runtime.list.return_value = [self._job()]
        user_message = HumanMessage(id="user-1", content="question")
        internal_retry = HumanMessage(
            id="retry-1",
            content="continue",
            additional_kwargs={"dataagent_internal_retry": True},
        )
        state = {"messages": [user_message, internal_retry]}

        update = self._middleware(runtime).before_model(
            cast(Any, state),
            cast(Any, MagicMock()),
        )

        assert update is not None
        updated_message = update["messages"][0]
        self.assertEqual(updated_message.id, user_message.id)
        self.assertEqual(
            updated_message.additional_kwargs[SHELL_JOB_CONTEXT_KEY],
            {
                "jobs": [
                    {
                        "job_id": "job_12345678",
                        "output_path": self._job().output_path,
                    }
                ]
            },
        )
        self.assertNotIn(SHELL_JOB_CONTEXT_KEY, user_message.additional_kwargs)
        merged = cast(
            list[Any],
            add_messages(
                cast(Any, state["messages"]),
                cast(Any, update["messages"]),
            ),
        )
        self.assertEqual(len(merged), 2)
        self.assertIn(SHELL_JOB_CONTEXT_KEY, merged[0].additional_kwargs)

    def test_before_model_does_not_change_frozen_snapshot(self) -> None:
        runtime = MagicMock()
        runtime.list.return_value = [self._job("job_newjob1")]
        frozen = HumanMessage(
            id="user-1",
            content="question",
            additional_kwargs={
                SHELL_JOB_CONTEXT_KEY: {
                    "jobs": [
                        {
                            "job_id": "job_oldjob11",
                            "output_path": "/workspace/job_oldjob11.log",
                        }
                    ]
                }
            },
        )

        update = self._middleware(runtime).before_model(
            cast(Any, {"messages": [frozen]}),
            cast(Any, MagicMock()),
        )

        self.assertIsNone(update)

    def test_frozen_context_is_appended_to_user_message_request_copy(self) -> None:
        runtime = MagicMock()
        user_message = HumanMessage(
            id="user-1",
            content="question",
            additional_kwargs={
                SHELL_JOB_CONTEXT_KEY: {
                    "jobs": [
                        {
                            "job_id": "job_12345678",
                            "output_path": self._job().output_path,
                        }
                    ]
                }
            },
        )
        request = self._request(user_message)
        captured: list[ModelRequest[Any]] = []

        def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(projected)
            return ModelResponse(result=[AIMessage(content="ok")])

        self._middleware(runtime).wrap_model_call(request, handler)

        self.assertIsNot(captured[0], request)
        assert request.system_message is not None
        assert captured[0].system_message is not None
        self.assertEqual(request.system_message.text, "base prompt")
        self.assertEqual(captured[0].system_message.text, "base prompt")
        self.assertEqual(user_message.content, "question")
        projected = captured[0].messages[0]
        self.assertIsInstance(projected, HumanMessage)
        assert isinstance(projected.content, list)
        self.assertEqual(projected.content[0], {"type": "text", "text": "question"})
        shell_block = cast(dict[str, Any], projected.content[1])["text"]
        self.assertIn("<shell_jobs>", shell_block)
        self.assertIn("job_12345678", shell_block)
        self.assertNotIn('"status"', shell_block)

    def test_request_without_frozen_context_is_not_copied(self) -> None:
        runtime = MagicMock()
        request = self._request(HumanMessage(id="user-1", content="question"))
        captured: list[ModelRequest[Any]] = []

        def handler(projected: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(projected)
            return ModelResponse(result=[AIMessage(content="ok")])

        self._middleware(runtime).wrap_model_call(request, handler)

        self.assertIs(captured[0], request)


if __name__ == "__main__":
    unittest.main()
