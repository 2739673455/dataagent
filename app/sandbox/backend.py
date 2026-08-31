"""DeepAgents Docker 沙箱 Backend。"""

import asyncio
import base64
import io
import json
import posixpath
import secrets
import shlex
import tarfile
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Literal, TypeVar
from uuid import UUID

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    DeleteResult,
    EditResult,
    ExecuteOffloadResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from app.sandbox.concurrency import LifecycleGuard
from app.sandbox.exceptions import SandboxPathError, SandboxStorageLimitError
from app.sandbox.ownership import SandboxOwnership
from app.sandbox.paths import (
    SANDBOX_DATA_ROOT,
    SANDBOX_STAGING_ROOT,
    SandboxSessionScope,
)
from app.sandbox.scripts import (
    _CANCEL_SHELL_JOB_SCRIPT,
    _COMMIT_UPLOAD_SCRIPT,
    _LARGE_EDIT_SCRIPT,
    _SHELL_JOB_STARTED_MARKER,
    _SHELL_JOB_WRAPPER_SCRIPT,
)
from app.shared.config.app_config import SandboxConfig

_ResultT = TypeVar("_ResultT")
_SANDBOX_STAGING_ROOT = SANDBOX_STAGING_ROOT
_SHELL_JOB_INLINE_BYTES = 80_000
_SHELL_JOB_CANCEL_GRACE_SECONDS = 1.0


def _close_exec_stream(stream: object) -> None:
    """关闭 Docker exec 流及其底层 HTTP 响应。"""
    close_stream = getattr(stream, "close", None)
    if callable(close_stream):
        close_stream()
    # Docker SDK 的可取消流持有 Response；显式关闭它可在提前取消时及时归还连接。
    response = getattr(stream, "_response", None)
    close_response = getattr(response, "close", None)
    if callable(close_response):
        close_response()


@dataclass(frozen=True, slots=True)
class SandboxShellJobExecution:
    """Sandbox Shell Job 的最终执行信息。"""

    status: Literal["completed", "failed", "interrupted"]
    exit_code: int | None = None
    output: str | None = None
    output_inline_truncated: bool = False
    output_truncated: bool = False
    workspace_limit_exceeded: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxShellJobCancellation:
    """Sandbox 进程组取消结果。"""

    ready: bool
    signal_sent: bool
    exited: bool


class DockerSandboxBackend(BaseSandbox):
    """将一个用户容器中的会话目录暴露为虚拟文件系统。"""

    enable_capture_offload = True

    def __init__(
        self,
        user_id: int,
        conversation_id: UUID,
        conversation_uid: int,
        sandbox_config: SandboxConfig,
        ownership: SandboxOwnership,
        user_guard: LifecycleGuard,
        conversation_guard: LifecycleGuard,
        mutation_lock: threading.RLock,
        touch: Callable[[], None],
        get_running_container: Callable[[threading.Event | None], Container],
        notify_capacity_waiters: Callable[[], None],
        *,
        session_scope: SandboxSessionScope | None = None,
        execution_uid: int | None = None,
    ) -> None:
        """初始化会话级 Docker 沙箱后端。"""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._conversation_dir = f"{SANDBOX_DATA_ROOT}/{conversation_id}"
        self._session_scope = session_scope
        self._workspace_dir = (
            posixpath.join(
                self._conversation_dir,
                session_scope.relative_workspace,
            )
            if session_scope is not None
            else self._conversation_dir
        )
        self._conversation_uid = conversation_uid
        self._execution_uid = execution_uid or conversation_uid
        self._execution_gid = conversation_uid
        self._file_mode = 0o640 if session_scope is not None else 0o600
        self._directory_mode = 0o750 if session_scope is not None else 0o700
        self._umask = 0o027 if session_scope is not None else 0o077
        self._internal_command_timeout_seconds = (
            sandbox_config.internal_command_timeout_seconds
        )
        self._staging_dir = posixpath.join(
            _SANDBOX_STAGING_ROOT,
            str(conversation_id),
            str(self._execution_uid),
        )
        self._max_file_bytes = sandbox_config.max_file_bytes
        self._max_workspace_bytes = sandbox_config.max_workspace_bytes
        self._ownership = ownership
        self._user_guard = user_guard
        self._conversation_guard = conversation_guard
        self._mutation_lock = mutation_lock
        self._touch = touch
        self._get_running_container = get_running_container
        self._notify_capacity_waiters = notify_capacity_waiters
        self._operation_local = threading.local()

    @property
    def _container(self) -> Container:
        """获取当前操作持有的容器实例。"""
        container = getattr(self._operation_local, "container", None)
        if container is None:
            raise RuntimeError("Docker 容器仅在操作期间可用")
        return container

    @property
    def id(self) -> str:
        """获取沙箱后端唯一标识。"""
        scope = (
            f":{self._session_scope.relative_workspace}"
            if self._session_scope is not None
            else ""
        )
        return f"docker:{self._user_id}:{self._conversation_id}{scope}"

    @property
    def workspace_dir(self) -> str:
        """获取会话在容器中的实际工作目录。"""
        return self._workspace_dir

    def _resolve_path(self, path: str) -> str:
        """将虚拟路径映射到当前会话目录。"""
        if "\x00" in path or path.startswith("~"):
            raise SandboxPathError(path)

        if path == self._workspace_dir or path.startswith(f"{self._workspace_dir}/"):
            return path
        if path == self._conversation_dir or path.startswith(
            f"{self._conversation_dir}/"
        ):
            return path

        parts = PurePosixPath(path).parts
        if any(part == ".." for part in parts):
            raise SandboxPathError(path)
        if PurePosixPath(path).is_absolute():
            return posixpath.join(self._conversation_dir, *parts[1:])
        return posixpath.join(self._workspace_dir, *parts)

    def _resolve_mutation_path(self, path: str) -> str:
        """只允许修改当前 Agent Session 工作区。"""
        resolved_path = self._resolve_path(path)
        if self._session_scope is not None and not (
            resolved_path == self._workspace_dir
            or resolved_path.startswith(f"{self._workspace_dir}/")
        ):
            raise SandboxPathError(path)
        return resolved_path

    def _to_virtual_path(self, path: str) -> str:
        """将容器路径还原为 Agent 可见的虚拟路径。"""
        if path == self._conversation_dir:
            return "/"
        prefix = f"{self._conversation_dir}/"
        if path.startswith(prefix):
            return f"/{path[len(prefix) :]}"
        if not path.startswith("/"):
            normalized_path = PurePosixPath(path).as_posix()
            return f"/{normalized_path}" if normalized_path != "." else "/"
        return path

    def _hide_workspace(self, message: str | None) -> str | None:
        """从错误信息中隐藏容器工作目录。"""
        if message is None:
            return None
        return message.replace(self._conversation_dir, "").replace(
            self._staging_dir,
            "<sandbox-staging>",
        )

    def _map_file_info(self, info: FileInfo) -> FileInfo:
        """转换文件信息中的路径。"""
        return FileInfo(**{**info, "path": self._to_virtual_path(info["path"])})

    def _map_grep_match(self, match: GrepMatch) -> GrepMatch:
        """转换搜索结果中的路径。"""
        return GrepMatch(**{**match, "path": self._to_virtual_path(match["path"])})

    @contextmanager
    def _resolved_operation(
        self,
        path: str,
        *,
        mutation: bool = False,
    ) -> Generator[str | None, None, None]:
        """解析路径并进入沙箱操作窗口。"""
        try:
            resolved_path = (
                self._resolve_mutation_path(path)
                if mutation
                else self._resolve_path(path)
            )
        except SandboxPathError:
            yield None
            return
        with self._operation():
            yield resolved_path

    @contextmanager
    def _operation(self) -> Generator[None, None, None]:
        """在资源生命周期保护下执行沙箱操作。"""
        self._touch()
        existing_container = getattr(self._operation_local, "container", None)
        cancel_event = getattr(self._operation_local, "cancel_event", None)
        try:
            with (
                self._ownership.operation(
                    self._user_id,
                    self._conversation_id,
                ),
                self._user_guard.operation(),
                self._conversation_guard.operation(),
            ):
                if existing_container is None:
                    self._operation_local.container = self._get_running_container(
                        cancel_event
                    )
                yield
        finally:
            if existing_container is None and hasattr(
                self._operation_local, "container"
            ):
                del self._operation_local.container
            self._touch()

    async def _run_async(
        self,
        operation: Callable[[], _ResultT],
    ) -> _ResultT:
        """在线程中运行同步操作并向容量等待传播任务取消。"""
        cancel_event = threading.Event()

        def run() -> _ResultT:
            """在线程本地上下文中执行可取消操作。"""
            self._operation_local.cancel_event = cancel_event
            try:
                return operation()
            finally:
                del self._operation_local.cancel_event

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            return await task
        except asyncio.CancelledError:
            cancel_event.set()
            self._notify_capacity_waiters()
            raise

    def _execute_unlocked(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """流式执行命令并限制宿主机保留的输出。"""
        effective_timeout = self._internal_command_timeout_seconds
        if timeout is not None and timeout > 0:
            effective_timeout = min(timeout, self._internal_command_timeout_seconds)
        file_limit_blocks = max(1, self._max_file_bytes // 512)
        command_shell = (
            f"umask {self._umask:03o}; ulimit -f {file_limit_blocks}; "
            f"exec /bin/sh -lc {shlex.quote(command)}"
        )
        shell_command = ["/bin/sh", "-lc", command_shell]
        if effective_timeout > 0:
            shell_command = [
                "timeout",
                "--signal=KILL",
                str(effective_timeout),
                *shell_command,
            ]

        docker_client = self._container.client
        if docker_client is None:
            raise RuntimeError("Docker 容器客户端不可用")
        api_client = docker_client.api
        created = api_client.exec_create(
            self._container.id,
            shell_command,
            stdout=True,
            stderr=True,
            user=f"{self._execution_uid}:{self._execution_gid}",
            environment={
                "HOME": f"{self._workspace_dir}/.home",
                "UV_CACHE_DIR": f"{self._workspace_dir}/.cache/uv",
                "XDG_CACHE_HOME": f"{self._workspace_dir}/.cache",
                "TMPDIR": f"{self._workspace_dir}/.tmp",
                "TMP": f"{self._workspace_dir}/.tmp",
                "TEMP": f"{self._workspace_dir}/.tmp",
                "DATAAGENT_CONVERSATION_ROOT": self._conversation_dir,
            },
            workdir=self._workspace_dir,
        )
        exec_id = created["Id"]
        output_buffer = bytearray()
        output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
        try:
            for chunk in output_stream:
                output_buffer.extend(chunk)
        finally:
            _close_exec_stream(output_stream)

        inspected = api_client.exec_inspect(exec_id)
        output = output_buffer.decode("utf-8", errors="replace")
        return ExecuteResponse(
            output=self._hide_workspace(output) or "",
            exit_code=inspected.get("ExitCode"),
        )

    def _workspace_size_unlocked(self) -> int:
        """读取当前会话目录占用的字节数。"""
        result = self._container.exec_run(
            [
                "timeout",
                "--signal=KILL",
                str(self._internal_command_timeout_seconds),
                "du",
                "-sb",
                self._conversation_dir,
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
            detail = self._hide_workspace(output.strip())
            raise OSError(detail or "查询工作区大小失败")
        try:
            return int(output.split(maxsplit=1)[0])
        except ValueError as exc:
            raise OSError("工作区大小响应格式无效") from exc

    def _validate_workspace_capacity_unlocked(
        self,
        incoming_bytes: int,
        replaced_bytes: int = 0,
    ) -> None:
        """校验写入后工作区不会超过容量限制。"""
        projected_size = (
            self._workspace_size_unlocked() - replaced_bytes + incoming_bytes
        )
        if projected_size > self._max_workspace_bytes:
            raise SandboxStorageLimitError(
                f"工作区存储空间超出限制: {projected_size} > {self._max_workspace_bytes}"
            )

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """在用户容器的当前会话目录中执行命令。"""
        with self._operation():
            if self._workspace_size_unlocked() > self._max_workspace_bytes:
                return ExecuteResponse(
                    output="Workspace storage limit exceeded; delete files before continuing",
                    exit_code=1,
                )
            result = self._execute_unlocked(command, timeout=timeout)
            if self._workspace_size_unlocked() > self._max_workspace_bytes:
                result.output += "\n[Workspace storage limit exceeded; delete files before continuing]"
                result.truncated = True
            return result

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """异步执行命令并支持取消容量等待。"""
        return await self._run_async(lambda: self.execute(command, timeout=timeout))

    @staticmethod
    def _validate_shell_job_id(job_id: str) -> None:
        """只接受 Runtime 生成的短随机 Shell Job 标识。"""
        if (
            len(job_id) != 12
            or not job_id.startswith("job_")
            or any(character not in "0123456789abcdef" for character in job_id[4:])
        ):
            raise ValueError("Shell Job 标识无效")

    def _shell_job_paths(self, job_id: str) -> tuple[str, str, str]:
        """生成受控日志路径、虚拟路径和内部控制路径。"""
        self._validate_shell_job_id(job_id)
        relative_log_path = f"large_tool_results/shell_jobs/{job_id}.log"
        return (
            posixpath.join(self._workspace_dir, relative_log_path),
            relative_log_path,
            posixpath.join(self._staging_dir, "shell_jobs", f"{job_id}.json"),
        )

    def _read_shell_job_control_unlocked(
        self,
        control_path: str,
    ) -> dict[str, object] | None:
        """以 root 身份读取模型不可见的 Shell Job 控制文件。"""
        result = self._container.exec_run(
            [
                "timeout",
                "--signal=KILL",
                str(self._internal_command_timeout_seconds),
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

    def _run_shell_job_unlocked(
        self,
        job_id: str,
        command: str,
        started_callback: Callable[[], None] | None,
    ) -> SandboxShellJobExecution:
        """启动包装进程并持续监控到业务命令终态。"""
        log_path, _, control_path = self._shell_job_paths(job_id)
        if self._workspace_size_unlocked() > self._max_workspace_bytes:
            return SandboxShellJobExecution(
                status="failed",
                error="Workspace storage limit exceeded; delete files before continuing",
                workspace_limit_exceeded=True,
            )

        payload = base64.b64encode(
            json.dumps(
                {
                    "workspace": self._workspace_dir,
                    "staging": self._staging_dir,
                    "job_id": job_id,
                    "command": command,
                    "owner_uid": self._execution_uid,
                    "owner_gid": self._execution_gid,
                    "file_mode": self._file_mode,
                    "directory_mode": self._directory_mode,
                    "umask": self._umask,
                    "max_file_bytes": self._max_file_bytes,
                },
                separators=(",", ":"),
            ).encode()
        ).decode()
        docker_client = self._container.client
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
                self._container.id,
                ["python3", "-c", _SHELL_JOB_WRAPPER_SCRIPT, payload],
                stdout=True,
                stderr=True,
                user="0",
                privileged=True,
                environment={
                    "HOME": f"{self._workspace_dir}/.home",
                    "UV_CACHE_DIR": f"{self._workspace_dir}/.cache/uv",
                    "XDG_CACHE_HOME": f"{self._workspace_dir}/.cache",
                    "TMPDIR": f"{self._workspace_dir}/.tmp",
                    "TMP": f"{self._workspace_dir}/.tmp",
                    "TEMP": f"{self._workspace_dir}/.tmp",
                    "DATAAGENT_CONVERSATION_ROOT": self._conversation_dir,
                },
                workdir=self._workspace_dir,
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
            detail = self._hide_workspace(str(exc).strip())
            return SandboxShellJobExecution(
                status="interrupted" if started else "failed",
                error=detail or type(exc).__name__,
            )
        finally:
            if output_stream is not None:
                _close_exec_stream(output_stream)

        control = self._read_shell_job_control_unlocked(control_path)
        if control is None:
            diagnostic_text = diagnostics.decode("utf-8", errors="replace").strip()
            detail = self._hide_workspace(diagnostic_text)
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
                error=self._hide_workspace(
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

        output_bytes, read_exit_code = self._read_limited_file_bytes_unlocked(
            log_path,
            _SHELL_JOB_INLINE_BYTES + 1,
        )
        inline_truncated = len(output_bytes) > _SHELL_JOB_INLINE_BYTES
        if inline_truncated:
            output_bytes = output_bytes[:_SHELL_JOB_INLINE_BYTES]
        output = output_bytes.decode("utf-8", errors="replace")
        if read_exit_code != 0:
            output = ""
        workspace_limit_exceeded = (
            self._workspace_size_unlocked() > self._max_workspace_bytes
        )
        observed_exit_code = normalized_exit_code
        if inspected.get("ExitCode") is None:
            return SandboxShellJobExecution(
                status="interrupted",
                exit_code=observed_exit_code,
                output=output,
                output_inline_truncated=inline_truncated,
                output_truncated=output_truncated,
                workspace_limit_exceeded=workspace_limit_exceeded,
                error="Shell Job 包装进程状态不可用",
            )
        return SandboxShellJobExecution(
            status="completed" if observed_exit_code == 0 else "failed",
            exit_code=observed_exit_code,
            output=output,
            output_inline_truncated=inline_truncated,
            output_truncated=output_truncated,
            workspace_limit_exceeded=workspace_limit_exceeded,
        )

    def run_shell_job(
        self,
        job_id: str,
        command: str,
        started_callback: Callable[[], None] | None = None,
    ) -> SandboxShellJobExecution:
        """执行无固定总时限的 Specialist Shell Job。"""
        self._validate_shell_job_id(job_id)
        if not command.strip():
            raise ValueError("Shell 命令不能为空")
        try:
            with self._operation():
                return self._run_shell_job_unlocked(
                    job_id,
                    command,
                    started_callback,
                )
        except Exception as exc:  # noqa: BLE001
            detail = self._hide_workspace(str(exc).strip())
            return SandboxShellJobExecution(
                status="failed",
                error=detail or type(exc).__name__,
            )

    async def arun_shell_job(
        self,
        job_id: str,
        command: str,
        started_callback: Callable[[], None] | None = None,
    ) -> SandboxShellJobExecution:
        """在线程中运行 Shell Job，并让监控单元独立于工具等待。"""
        return await self._run_async(
            lambda: self.run_shell_job(
                job_id,
                command,
                started_callback,
            )
        )

    def cancel_shell_job(self, job_id: str) -> SandboxShellJobCancellation:
        """先 TERM 后 KILL 终止 Shell Job 的整个进程组。"""
        _, _, control_path = self._shell_job_paths(job_id)
        with self._operation():
            result = self._container.exec_run(
                [
                    "timeout",
                    "--signal=KILL",
                    str(self._internal_command_timeout_seconds),
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
            raise OSError(self._hide_workspace(output.strip()) or "取消 Shell Job 失败")
        try:
            response = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OSError("取消 Shell Job 的响应格式无效") from exc
        return SandboxShellJobCancellation(
            ready=response.get("ready") is True,
            signal_sent=response.get("signal_sent") is True,
            exited=response.get("exited") is True,
        )

    async def acancel_shell_job(self, job_id: str) -> SandboxShellJobCancellation:
        """异步取消 Shell Job。"""
        return await asyncio.to_thread(self.cancel_shell_job, job_id)

    def cleanup_shell_job_control(self, job_id: str) -> None:
        """清除单次 Agent Run 的内部 Shell Job 控制文件。"""
        _, _, control_path = self._shell_job_paths(job_id)
        with self._operation():
            self._container.exec_run(
                ["rm", "-f", "--", control_path],
                user="0",
                privileged=True,
                workdir=SANDBOX_DATA_ROOT,
            )

    async def acleanup_shell_job_control(self, job_id: str) -> None:
        """异步清除 Shell Job 控制文件。"""
        await asyncio.to_thread(self.cleanup_shell_job_control, job_id)

    def execute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,
    ) -> ExecuteOffloadResult:
        """在会话目录中卸载大命令输出，并复用单文件容量上限。"""
        capture_limit = min(
            max_capture_bytes or self._max_file_bytes,
            self._max_file_bytes,
        )
        return super().execute_with_offload(
            command,
            self._resolve_mutation_path(capture_path),
            max_inline_bytes=max_inline_bytes,
            max_capture_bytes=capture_limit,
            timeout=timeout,
        )

    def ls(self, path: str) -> LsResult:
        """列出当前会话目录内容。"""
        with self._resolved_operation(path) as resolved_path:
            if resolved_path is None:
                return LsResult(error=INVALID_PATH)
            result = super().ls(resolved_path)
            return LsResult(
                error=self._hide_workspace(result.error),
                entries=(
                    [self._map_file_info(item) for item in result.entries]
                    if result.entries is not None
                    else None
                ),
            )

    async def als(self, path: str) -> LsResult:
        """异步列出当前会话目录内容。"""
        return await self._run_async(lambda: self.ls(path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读取当前会话文件。"""
        with self._resolved_operation(file_path) as resolved_path:
            if resolved_path is None:
                return ReadResult(error=INVALID_PATH)
            result = super().read(resolved_path, offset, limit)
            result.error = self._hide_workspace(result.error)
            return result

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """异步读取当前会话文件。"""
        return await self._run_async(lambda: self.read(file_path, offset, limit))

    def write(self, file_path: str, content: str) -> WriteResult:
        """写入当前会话文件。"""
        with self._resolved_operation(file_path, mutation=True) as resolved_path:
            if resolved_path is None:
                return WriteResult(error=INVALID_PATH)
            result = super().write(resolved_path, content)
            return WriteResult(
                error=self._hide_workspace(result.error),
                path=self._to_virtual_path(result.path) if result.path else None,
            )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """异步写入当前会话文件。"""
        return await self._run_async(lambda: self.write(file_path, content))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """编辑当前会话文件。"""
        with self._resolved_operation(file_path, mutation=True) as resolved_path:
            if resolved_path is None:
                return EditResult(error=INVALID_PATH)
            with self._mutation_lock:
                result = self._edit_file(
                    resolved_path,
                    old_string,
                    new_string,
                    replace_all,
                )
            return EditResult(
                error=self._hide_workspace(result.error),
                path=self._to_virtual_path(result.path) if result.path else None,
                occurrences=result.occurrences,
            )

    def _edit_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
    ) -> EditResult:
        """通过会话目录内的临时文件安全编辑文本。"""
        token = secrets.token_hex(10)
        old_path = self._resolve_mutation_path(f".deepagents_tmp/{token}.old")
        new_path = self._resolve_mutation_path(f".deepagents_tmp/{token}.new")
        responses = self.upload_files(
            [
                (old_path, old_string.encode()),
                (new_path, new_string.encode()),
            ]
        )
        if error := next((item.error for item in responses if item.error), None):
            self._execute_unlocked(
                f"rm -f {shlex.quote(old_path)} {shlex.quote(new_path)}"
            )
            return EditResult(error=f"编辑文件 '{file_path}' 失败: {error}")

        payload = base64.b64encode(
            json.dumps(
                {
                    "target": file_path,
                    "old": old_path,
                    "new": new_path,
                    "replace_all": replace_all,
                    "workspace": self._workspace_dir,
                    "max_file_bytes": self._max_file_bytes,
                    "max_workspace_bytes": self._max_workspace_bytes,
                }
            ).encode()
        ).decode()
        result = self.execute(
            f"python3 -c {shlex.quote(_LARGE_EDIT_SCRIPT)} {shlex.quote(payload)}"
        )
        try:
            response = json.loads(result.output)
        except json.JSONDecodeError:
            self._execute_unlocked(
                f"rm -f {shlex.quote(old_path)} {shlex.quote(new_path)}"
            )
            detail = result.output.strip() or "未知错误"
            return EditResult(error=f"编辑文件 '{file_path}' 失败: {detail}")
        if error := response.get("error"):
            return EditResult(error=f"编辑文件 '{file_path}' 失败: {error}")
        return EditResult(path=file_path, occurrences=response.get("count", 1))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """异步编辑当前会话文件。"""
        return await self._run_async(
            lambda: self.edit(
                file_path,
                old_string,
                new_string,
                replace_all,
            )
        )

    def delete(self, file_path: str) -> DeleteResult:
        """删除当前会话文件或目录。"""
        with self._resolved_operation(file_path, mutation=True) as resolved_path:
            if resolved_path is None:
                return DeleteResult(error=INVALID_PATH)
            with self._mutation_lock:
                result = super().delete(resolved_path)
            return DeleteResult(
                error=self._hide_workspace(result.error),
                path=self._to_virtual_path(result.path) if result.path else None,
            )

    async def adelete(self, file_path: str) -> DeleteResult:
        """异步删除当前会话文件或目录。"""
        return await self._run_async(lambda: self.delete(file_path))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """搜索当前会话文件内容。"""
        with self._resolved_operation(path or "/") as resolved_path:
            if resolved_path is None:
                return GrepResult(error=INVALID_PATH)
            result = super().grep(
                pattern,
                resolved_path,
                glob,
                max_count=max_count,
            )
            return GrepResult(
                error=self._hide_workspace(result.error),
                matches=(
                    [self._map_grep_match(item) for item in result.matches]
                    if result.matches is not None
                    else None
                ),
                truncated=result.truncated,
            )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """异步搜索当前会话文件内容。"""
        return await self._run_async(
            lambda: self.grep(
                pattern,
                path,
                glob,
                max_count=max_count,
            )
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """匹配当前会话中的文件。"""
        with self._resolved_operation(path or "/") as resolved_path:
            if resolved_path is None:
                return GlobResult(error=INVALID_PATH)
            result = super().glob(pattern, resolved_path)
            return GlobResult(
                error=self._hide_workspace(result.error),
                matches=(
                    [self._map_file_info(item) for item in result.matches]
                    if result.matches is not None
                    else None
                ),
                truncated=result.truncated,
            )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """异步匹配当前会话中的文件。"""
        return await self._run_async(lambda: self.glob(pattern, path))

    def _put_archive(self, path: str, content: BinaryIO, size: int) -> None:
        """先写入受保护的暂存目录，再提交到当前可写根。"""
        relative_target = posixpath.relpath(path, self._workspace_dir)
        if relative_target == "." or relative_target.startswith("../"):
            raise SandboxPathError(path)
        staging_name = f"upload-{secrets.token_hex(20)}"
        staging_path = posixpath.join(self._staging_dir, staging_name)
        try:
            with io.BytesIO() as archive_buffer:
                with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
                    info = tarfile.TarInfo(name=staging_name)
                    info.size = size
                    info.mode = 0o600
                    info.uid = 0
                    info.gid = 0
                    archive.addfile(info, content)
                archive_buffer.seek(0)
                if not self._container.put_archive(self._staging_dir, archive_buffer):
                    raise OSError(f"暂存上传文件失败: {path}")

            payload = base64.b64encode(
                json.dumps(
                    {
                        "root": self._workspace_dir,
                        "source": staging_path,
                        "owner_uid": self._execution_uid,
                        "owner_gid": self._execution_gid,
                        "file_mode": self._file_mode,
                        "directory_mode": self._directory_mode,
                        "relative_target": relative_target,
                    }
                ).encode()
            ).decode()
            commit_result = self._container.exec_run(
                ["python3", "-c", _COMMIT_UPLOAD_SCRIPT, payload],
                user="0",
                privileged=True,
                workdir=SANDBOX_DATA_ROOT,
            )
            if commit_result.exit_code != 0:
                raw_output = commit_result.output or b""
                detail = (
                    raw_output.decode("utf-8", errors="replace")
                    if isinstance(raw_output, bytes)
                    else str(raw_output)
                ).strip()
                raise OSError(f"提交上传文件失败: {detail}")
        finally:
            self._container.exec_run(
                ["rm", "-f", "--", staging_path],
                user="0",
                privileged=True,
                workdir=SANDBOX_DATA_ROOT,
            )

    def _read_limited_file_bytes_unlocked(
        self,
        path: str,
        max_bytes: int,
    ) -> tuple[bytes, int | None]:
        """以会话 UID 限长读取文件，避免 Docker 守护进程绕过权限。"""
        docker_client = self._container.client
        if docker_client is None:
            raise RuntimeError("Docker 容器客户端不可用")
        api_client = docker_client.api
        created = api_client.exec_create(
            self._container.id,
            [
                "timeout",
                "--signal=KILL",
                str(self._internal_command_timeout_seconds),
                "head",
                "-c",
                str(max_bytes),
                "--",
                path,
            ],
            stdout=True,
            stderr=True,
            user=f"{self._execution_uid}:{self._execution_gid}",
            environment={"HOME": f"{self._workspace_dir}/.home"},
            workdir=self._workspace_dir,
        )
        exec_id = created["Id"]
        output = bytearray()
        output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
        try:
            for chunk in output_stream:
                output.extend(chunk)
        finally:
            _close_exec_stream(output_stream)
        inspected = api_client.exec_inspect(exec_id)
        return bytes(output), inspected.get("ExitCode")

    def _read_file_bytes_unlocked(self, path: str) -> tuple[bytes, int | None]:
        """按单文件上限读取文件内容。"""
        return self._read_limited_file_bytes_unlocked(
            path,
            self._max_file_bytes + 1,
        )

    def _file_size_unlocked(self, path: str) -> int:
        """读取文件字节数，不存在时返回零。"""
        result = self._execute_unlocked(
            f"if [ -f {shlex.quote(path)} ]; then stat -c %s -- {shlex.quote(path)}; else printf 0; fi"
        )
        if result.exit_code != 0:
            raise OSError(result.output.strip() or f"读取文件元数据失败: {path}")
        try:
            return int(result.output.strip())
        except ValueError as exc:
            raise OSError(f"文件大小响应格式无效: {path}") from exc

    def upload_fileobj(self, path: str, content: BinaryIO) -> FileUploadResponse:
        """上传文件对象到当前会话。"""
        try:
            resolved_path = self._resolve_mutation_path(path)
            with self._operation(), self._mutation_lock:
                content.seek(0, io.SEEK_END)
                size = content.tell()
                content.seek(0)
                if size > self._max_file_bytes:
                    return FileUploadResponse(
                        path=path,
                        error=f"file_too_large:{self._max_file_bytes}",
                    )
                replaced_size = self._file_size_unlocked(resolved_path)
                self._validate_workspace_capacity_unlocked(size, replaced_size)
                self._put_archive(resolved_path, content, size)
        except SandboxPathError:
            return FileUploadResponse(path=path, error=INVALID_PATH)
        except SandboxStorageLimitError:
            return FileUploadResponse(
                path=path,
                error=f"workspace_limit_exceeded:{self._max_workspace_bytes}",
            )
        except (APIError, OSError, tarfile.TarError) as exc:
            return FileUploadResponse(path=path, error=str(exc))
        return FileUploadResponse(path=path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """批量上传字节内容到当前会话。"""
        return [
            self.upload_fileobj(path, io.BytesIO(content)) for path, content in files
        ]

    async def aupload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        """异步批量上传字节内容到当前会话。"""
        return await self._run_async(lambda: self.upload_files(files))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载当前会话文件。"""
        responses: list[FileDownloadResponse] = []
        with self._operation():
            for path in paths:
                try:
                    resolved_path = self._resolve_path(path)
                    inspect_result = self._execute_unlocked(
                        f"if [ -d {shlex.quote(resolved_path)} ]; then exit 45; "
                        f"elif [ ! -f {shlex.quote(resolved_path)} ]; then exit 44; "
                        f"else stat -c %s -- {shlex.quote(resolved_path)}; fi"
                    )
                    if inspect_result.exit_code == 44:
                        responses.append(
                            FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
                        )
                        continue
                    if inspect_result.exit_code == 45:
                        responses.append(
                            FileDownloadResponse(path=path, error=IS_DIRECTORY)
                        )
                        continue
                    if inspect_result.exit_code != 0:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=inspect_result.output.strip()
                                or "failed_to_inspect_file",
                            )
                        )
                        continue
                    try:
                        size = int(inspect_result.output.strip())
                    except ValueError:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error="invalid_file_size_response",
                            )
                        )
                        continue
                    if size > self._max_file_bytes:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=f"file_too_large:{self._max_file_bytes}",
                            )
                        )
                        continue
                    content, exit_code = self._read_file_bytes_unlocked(resolved_path)
                    if len(content) > self._max_file_bytes:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=f"file_too_large:{self._max_file_bytes}",
                            )
                        )
                        continue
                    if exit_code != 0:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=content.decode("utf-8", errors="replace").strip()
                                or "failed_to_read_file",
                            )
                        )
                        continue
                    responses.append(FileDownloadResponse(path=path, content=content))
                except SandboxPathError:
                    responses.append(
                        FileDownloadResponse(path=path, error=INVALID_PATH)
                    )
                except NotFound:
                    responses.append(
                        FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
                    )
                except (APIError, OSError) as exc:
                    responses.append(FileDownloadResponse(path=path, error=str(exc)))
        return responses

    async def adownload_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        """异步批量下载当前会话文件。"""
        return await self._run_async(lambda: self.download_files(paths))

    def is_file(self, path: str) -> bool:
        """检查当前会话路径是否为文件。"""
        resolved_path = self._resolve_path(path)
        with self._operation():
            result = self._execute_unlocked(f"test -f {shlex.quote(resolved_path)}")
            return result.exit_code == 0
