"""Docker Shell Job 的受控执行与取消。"""

from __future__ import annotations

import asyncio
import base64
import json
import posixpath
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

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
class SandboxShellJobExecution:
    """Sandbox Shell Job 的最终执行信息。"""

    status: Literal["completed", "failed", "interrupted"]
    exit_code: int | None = None
    output: str | None = None
    output_inline_truncated: bool = False
    output_truncated: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxShellJobCancellation:
    """Sandbox 进程组取消结果。"""

    ready: bool
    signal_sent: bool
    exited: bool


class DockerShellJobRunner:
    """在一个会话 Backend 的 operation lease 中运行长时 Shell Job。"""

    def __init__(self, backend: DockerSandboxBackend) -> None:
        """绑定 Shell Job 所属的会话 Backend。"""
        self._backend = backend

    @property
    def workspace_dir(self) -> str:
        """返回模型可访问的 Shell Job 日志目录根路径。"""
        return self._backend.workspace_dir

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        """只接受 Runtime 生成的短随机 Shell Job 标识。"""
        if (
            len(job_id) != 12
            or not job_id.startswith("job_")
            or any(character not in "0123456789abcdef" for character in job_id[4:])
        ):
            raise ValueError("Shell Job 标识无效")

    def _paths(self, job_id: str) -> tuple[str, str]:
        """生成受控日志路径和模型不可见的控制路径。"""
        self._validate_job_id(job_id)
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
    ) -> SandboxShellJobExecution:
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
            return SandboxShellJobExecution(
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
            return SandboxShellJobExecution(
                status="interrupted" if started else "failed",
                error=detail or type(exc).__name__,
            )
        finally:
            if output_stream is not None:
                close_stream = getattr(output_stream, "close", None)
                if callable(close_stream):
                    close_stream()
                response = getattr(output_stream, "_response", None)
                close_response = getattr(response, "close", None)
                if callable(close_response):
                    close_response()

        control = self._read_control(control_path)
        if control is None:
            diagnostic_text = diagnostics.decode("utf-8", errors="replace").strip()
            detail = backend._sanitize_output(diagnostic_text)
            return SandboxShellJobExecution(
                status="interrupted" if started else "failed",
                error=detail or "Shell Job 未产生可读取的最终状态",
            )
        control_status = control.get("status")
        exit_code = control.get("exit_code")
        normalized_exit_code = exit_code if isinstance(exit_code, int) else None
        output_truncated = control.get("output_truncated") is True
        if control_status == "failed":
            raw_error = control.get("error")
            return SandboxShellJobExecution(
                status="failed",
                exit_code=normalized_exit_code,
                output_truncated=output_truncated,
                error=backend._sanitize_output(
                    raw_error if isinstance(raw_error, str) else None
                ),
            )
        if control_status != "finished" or normalized_exit_code is None:
            return SandboxShellJobExecution(
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
            return SandboxShellJobExecution(
                status="interrupted",
                exit_code=normalized_exit_code,
                output=output,
                output_inline_truncated=inline_truncated,
                output_truncated=output_truncated,
                error="Shell Job 包装进程状态不可用",
            )
        return SandboxShellJobExecution(
            status="completed" if normalized_exit_code == 0 else "failed",
            exit_code=normalized_exit_code,
            output=output,
            output_inline_truncated=inline_truncated,
            output_truncated=output_truncated,
        )

    def run(
        self,
        job_id: str,
        command: str,
        started_callback: Callable[[], None] | None = None,
    ) -> SandboxShellJobExecution:
        """执行无固定总时限的 Specialist Shell Job。"""
        self._validate_job_id(job_id)
        if not command.strip():
            raise ValueError("Shell 命令不能为空")
        try:
            with self._backend._operation():
                return self._run_unlocked(job_id, command, started_callback)
        except Exception as exc:  # noqa: BLE001
            detail = self._backend._sanitize_output(str(exc).strip())
            return SandboxShellJobExecution(
                status="failed",
                error=detail or type(exc).__name__,
            )

    async def arun(
        self,
        job_id: str,
        command: str,
        started_callback: Callable[[], None] | None = None,
    ) -> SandboxShellJobExecution:
        """在线程中运行 Shell Job，并让监控独立于工具等待。"""
        return await self._backend._run_async(
            lambda: self.run(job_id, command, started_callback)
        )

    def cancel(self, job_id: str) -> SandboxShellJobCancellation:
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
        try:
            response = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OSError("取消 Shell Job 的响应格式无效") from exc
        return SandboxShellJobCancellation(
            ready=response.get("ready") is True,
            signal_sent=response.get("signal_sent") is True,
            exited=response.get("exited") is True,
        )

    async def acancel(self, job_id: str) -> SandboxShellJobCancellation:
        """异步取消 Shell Job。"""
        return await asyncio.to_thread(self.cancel, job_id)

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
