"""Docker Shell Job 的受控执行与取消。"""

from __future__ import annotations

import asyncio
import base64
import json
import posixpath
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.sandbox.docker_stream import close_exec_stream
from app.sandbox.paths import SANDBOX_DATA_ROOT
from app.sandbox.scripts import (
    _CANCEL_SHELL_JOB_SCRIPT,
    _SHELL_JOB_STARTED_MARKER,
    _SHELL_JOB_WRAPPER_SCRIPT,
)

if TYPE_CHECKING:
    from app.sandbox.backend import DockerSandboxBackend


_INLINE_OUTPUT_BYTES = 80_000
_SHELL_JOB_CANCEL_GRACE_SECONDS = 1.0
_OUTPUT_TRUNCATION_MARKER = b"\n...[middle output truncated]...\n"


@dataclass(frozen=True, slots=True)
class ShellResult:
    """Shell 执行结果与输出日志路径。"""

    status: Literal["completed", "failed", "interrupted"]
    exit_code: int | None = None
    output: str | None = None
    output_inline_truncated: bool = False
    output_truncated: bool = False
    error: str | None = None
    output_path: str | None = None


class DockerShellJobRunner:
    """在会话操作租约内执行 Shell 命令并管理进程组。"""

    def __init__(self, backend: DockerSandboxBackend) -> None:
        """绑定 Shell Job 所属的会话 Backend。"""
        self._backend = backend

    def run(
        self,
        job_id: str,
        command: str,
        started_callback: Callable[[], None] | None = None,
    ) -> ShellResult:
        """在会话工作目录执行 Shell 命令并等待结束。"""
        if not command.strip():
            raise ValueError("Shell 命令不能为空")
        try:
            with self._backend._operation():
                return self._run_unlocked(job_id, command, started_callback)
        except Exception as exc:  # noqa: BLE001
            detail = self._backend._sanitize_output(str(exc).strip())
            return ShellResult(
                status="failed",
                error=detail or type(exc).__name__,
            )

    async def arun(
        self,
        job_id: str,
        command: str,
    ) -> ShellResult:
        """等待命令结束；调用取消时终止进程组并等待执行线程退出。"""
        cancel_event = threading.Event()
        started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def notify_started() -> None:
            loop.call_soon_threadsafe(started.set)

        def run() -> ShellResult:
            self._backend._operation_local.cancel_event = cancel_event
            try:
                return self.run(job_id, command, notify_started)
            finally:
                del self._backend._operation_local.cancel_event

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel_event.set()

            async def stop() -> None:
                ready = asyncio.create_task(started.wait())
                try:
                    await asyncio.wait(
                        {ready, task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not task.done():
                        await self.acancel(job_id)
                    await task
                finally:
                    ready.cancel()
                    await asyncio.gather(ready, return_exceptions=True)

            cleanup = asyncio.create_task(stop())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
            raise

    def cancel(self, job_id: str) -> None:
        """先 TERM 后 KILL 终止 Shell Job 的整个进程组。"""
        _, control_path = self._paths(job_id)
        with self._backend._operation():
            result = self._backend._container.exec_run(
                [
                    "timeout",
                    "--signal=KILL",
                    str(self._backend._internal_command_timeout_seconds),
                    "python3",
                    "-c",
                    _CANCEL_SHELL_JOB_SCRIPT,
                    control_path,
                    str(_SHELL_JOB_CANCEL_GRACE_SECONDS),
                ],
                user="0",
                privileged=True,
                workdir=SANDBOX_DATA_ROOT,
            )
        raw_output = result.output or b""
        output = (
            raw_output.decode("utf-8", errors="replace")
            if isinstance(raw_output, bytes)
            else str(raw_output)
        )
        if result.exit_code != 0:
            raise OSError(
                self._backend._sanitize_output(output.strip()) or "取消 Shell Job 失败"
            )

    async def acancel(self, job_id: str) -> None:
        """异步取消 Shell Job。"""
        await asyncio.to_thread(self.cancel, job_id)

    def cleanup(self, job_id: str, *, remove_log: bool = False) -> None:
        """清除 Shell Job 控制文件，并按需移除未公开的日志文件。"""
        log_path, control_path = self._paths(job_id)
        paths = [control_path, *([log_path] if remove_log else [])]
        with self._backend._operation():
            self._backend._container.exec_run(
                ["rm", "-f", "--", *paths],
                user="0",
                privileged=True,
                workdir=SANDBOX_DATA_ROOT,
            )

    async def acleanup(self, job_id: str, *, remove_log: bool = False) -> None:
        """异步清除 Shell Job 控制文件，并按需移除日志文件。"""
        await asyncio.to_thread(self.cleanup, job_id, remove_log=remove_log)

    def _paths(self, job_id: str) -> tuple[str, str]:
        """生成受控日志路径和模型不可见的控制路径。"""
        relative_log_path = f"large_tool_results/shell_jobs/{job_id}.log"
        return (
            posixpath.join(self._backend.workspace_dir, relative_log_path),
            posixpath.join(self._backend._staging_dir, "shell_jobs", f"{job_id}.json"),
        )

    def _read_control(self, control_path: str) -> dict[str, object] | None:
        """以 root 身份读取模型不可见的 Shell Job 控制文件。"""
        result = self._backend._container.exec_run(
            [
                "timeout",
                "--signal=KILL",
                str(self._backend._internal_command_timeout_seconds),
                "cat",
                "--",
                control_path,
            ],
            user="0",
            privileged=True,
            workdir=SANDBOX_DATA_ROOT,
        )
        if result.exit_code != 0:
            return None
        raw_output = result.output or b""
        output = (
            raw_output.decode("utf-8", errors="replace")
            if isinstance(raw_output, bytes)
            else str(raw_output)
        )
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _run_unlocked(
        self,
        job_id: str,
        command: str,
        started_callback: Callable[[], None] | None,
    ) -> ShellResult:
        """启动包装进程并持续监控到业务命令终态。"""
        log_path, control_path = self._paths(job_id)
        backend = self._backend
        payload = base64.b64encode(
            json.dumps(
                {
                    "workspace": backend.workspace_dir,
                    "staging": backend._staging_dir,
                    "job_id": job_id,
                    "command": command,
                    "owner_uid": backend._execution_uid,
                    "owner_gid": backend._execution_gid,
                    "file_mode": backend._file_mode,
                    "directory_mode": backend._directory_mode,
                    "umask": backend._umask,
                    "max_file_bytes": backend._max_file_bytes,
                },
                separators=(",", ":"),
            ).encode()
        ).decode()
        docker_client = backend._container.client
        if docker_client is None:
            return ShellResult(
                status="failed",
                error="Docker 容器客户端不可用",
            )
        api_client = docker_client.api
        diagnostics = bytearray()
        started = False
        started_notified = False
        started_marker = _SHELL_JOB_STARTED_MARKER.encode()
        output_stream: object | None = None
        try:
            created = api_client.exec_create(
                backend._container.id,
                ["python3", "-c", _SHELL_JOB_WRAPPER_SCRIPT, payload],
                stdout=True,
                stderr=True,
                user="0",
                privileged=True,
                environment={
                    "HOME": f"{backend.workspace_dir}/.home",
                    "UV_CACHE_DIR": f"{backend.workspace_dir}/.cache/uv",
                    "XDG_CACHE_HOME": f"{backend.workspace_dir}/.cache",
                    "TMPDIR": f"{backend.workspace_dir}/.tmp",
                    "TMP": f"{backend.workspace_dir}/.tmp",
                    "TEMP": f"{backend.workspace_dir}/.tmp",
                },
                workdir=backend.workspace_dir,
            )
            exec_id = created["Id"]
            output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
            started = True
            for raw_chunk in output_stream:
                if len(diagnostics) < 16_384:
                    diagnostics.extend(raw_chunk[: 16_384 - len(diagnostics)])
                if not started_notified and started_marker in diagnostics:
                    started_notified = True
                    diagnostics = bytearray(
                        bytes(diagnostics).replace(started_marker, b"").strip()
                    )
                    if started_callback is not None:
                        started_callback()
            inspected = api_client.exec_inspect(exec_id)
        except Exception as exc:  # noqa: BLE001
            detail = backend._sanitize_output(str(exc).strip())
            return ShellResult(
                status="interrupted" if started else "failed",
                error=detail or type(exc).__name__,
            )
        finally:
            if output_stream is not None:
                close_exec_stream(output_stream)

        control = self._read_control(control_path)
        if control is None:
            diagnostic_text = diagnostics.decode("utf-8", errors="replace").strip()
            detail = backend._sanitize_output(diagnostic_text)
            return ShellResult(
                status="interrupted" if started else "failed",
                error=detail or "Shell Job 未产生可读取的最终状态",
            )
        control_status = control.get("status")
        exit_code = control.get("exit_code")
        normalized_exit_code = exit_code if isinstance(exit_code, int) else None
        output_truncated = control.get("output_truncated") is True
        if control_status == "failed":
            raw_error = control.get("error")
            return ShellResult(
                status="failed",
                exit_code=normalized_exit_code,
                output_truncated=output_truncated,
                error=backend._sanitize_output(
                    raw_error if isinstance(raw_error, str) else None
                ),
            )
        if control_status != "finished" or normalized_exit_code is None:
            return ShellResult(
                status="interrupted",
                exit_code=normalized_exit_code,
                output_truncated=output_truncated,
                error="Shell Job 最终状态无效",
            )

        output_bytes, read_exit_code = backend._read_limited_file_bytes_unlocked(
            log_path,
            _INLINE_OUTPUT_BYTES + 1,
        )
        inline_truncated = len(output_bytes) > _INLINE_OUTPUT_BYTES
        if inline_truncated:
            head_bytes = (_INLINE_OUTPUT_BYTES + 1) // 2
            tail_bytes = _INLINE_OUTPUT_BYTES - head_bytes
            tail_output, tail_exit_code = backend._read_limited_file_bytes_unlocked(
                log_path,
                tail_bytes,
                from_end=True,
            )
            output_bytes = (
                output_bytes[:head_bytes]
                + _OUTPUT_TRUNCATION_MARKER
                + tail_output[-tail_bytes:]
            )
            if tail_exit_code != 0:
                read_exit_code = tail_exit_code
        output = output_bytes.decode("utf-8", errors="replace")
        if read_exit_code != 0:
            output = ""
        if inspected.get("ExitCode") is None:
            return ShellResult(
                status="interrupted",
                exit_code=normalized_exit_code,
                output=output,
                output_inline_truncated=inline_truncated,
                output_truncated=output_truncated,
                error="Shell Job 包装进程状态不可用",
            )
        return ShellResult(
            status="completed" if normalized_exit_code == 0 else "failed",
            exit_code=normalized_exit_code,
            output=output,
            output_inline_truncated=inline_truncated,
            output_truncated=output_truncated,
        )
