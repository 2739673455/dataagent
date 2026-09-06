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
from typing import BinaryIO, TypeVar
from uuid import UUID

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    ReadResult,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from app.sandbox.exceptions import SandboxPathError
from app.sandbox.ownership import SandboxOwnership
from app.sandbox.paths import (
    SANDBOX_DATA_ROOT,
    SANDBOX_STAGING_ROOT,
    SandboxSessionScope,
    conversation_workspace_path,
    resolve_sandbox_path,
)
from app.sandbox.scripts import (
    _COMMIT_UPLOAD_SCRIPT,
    _LARGE_EDIT_SCRIPT,
)
from app.sandbox.shell_runner import DockerShellJobRunner
from app.shared.config.app_config import SandboxConfig

_ResultT = TypeVar("_ResultT")
_SANDBOX_STAGING_ROOT = SANDBOX_STAGING_ROOT
_INLINE_OUTPUT_BYTES = 80_000
_SHELL_JOB_CANCEL_GRACE_SECONDS = 1.0
_OUTPUT_TRUNCATION_MARKER = b"\n...[middle output truncated]...\n"


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


class DockerSandboxBackend(BaseSandbox):
    """在一个用户容器中执行受 Conversation 和 Session 隔离的操作。"""

    def __init__(
        self,
        user_id: int,
        conversation_id: UUID,
        conversation_uid: int,
        sandbox_config: SandboxConfig,
        ownership: SandboxOwnership,
        touch: Callable[[], None],
        get_running_container: Callable[[threading.Event | None], Container],
        *,
        session_scope: SandboxSessionScope | None = None,
        execution_uid: int | None = None,
    ) -> None:
        """初始化会话级 Docker 沙箱后端。"""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._conversation_dir = conversation_workspace_path(conversation_id)
        self._session_scope = session_scope
        self._workspace_dir = (
            session_scope.workspace_path(conversation_id)
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
        self._ownership = ownership
        self._touch = touch
        self._get_running_container = get_running_container
        self._operation_local = threading.local()
        self.shell_jobs = DockerShellJobRunner(self)

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

    @property
    def conversation_dir(self) -> str:
        """获取当前 Conversation 在容器中的实际根目录。"""
        return self._conversation_dir

    def _resolve_path(self, path: str) -> str:
        """按 execute 的工作目录语义解析文件工具路径。"""
        return resolve_sandbox_path(path, self._workspace_dir)

    def _resolve_mutation_path(self, path: str) -> str:
        """只允许文件工具修改自身工作目录。"""
        resolved_path = self._resolve_path(path)
        if not (
            resolved_path == self._workspace_dir
            or resolved_path.startswith(f"{self._workspace_dir}/")
        ):
            raise SandboxPathError(path)
        return resolved_path

    def _sanitize_output(self, message: str | None) -> str | None:
        """隐藏仅供 Backend 内部使用的暂存目录。"""
        if message is None:
            return None
        return message.replace(self._staging_dir, "<sandbox-staging>")

    @contextmanager
    def _resolved_operation(
        self,
        path: str,
        *,
        mutation: bool = False,
    ) -> Generator[str | None]:
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
    def _operation(self) -> Generator[None]:
        """登记 Redis operation lease，并在公开操作结束后记录活动时间。"""
        existing_container = getattr(self._operation_local, "container", None)
        cancel_event = getattr(self._operation_local, "cancel_event", None)
        try:
            with self._ownership.operation(self._user_id, self._conversation_id):
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
            },
            workdir=self._workspace_dir,
        )
        exec_id = created["Id"]
        head_limit = (_INLINE_OUTPUT_BYTES + 1) // 2
        tail_limit = _INLINE_OUTPUT_BYTES - head_limit
        output_head = bytearray()
        output_tail = bytearray()
        output_size = 0
        output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
        try:
            for chunk in output_stream:
                output_size += len(chunk)
                head_remaining = head_limit - len(output_head)
                if head_remaining > 0:
                    head_chunk = chunk[:head_remaining]
                    output_head.extend(head_chunk)
                    chunk = chunk[len(head_chunk) :]
                if not chunk or tail_limit == 0:
                    continue
                if len(chunk) >= tail_limit:
                    output_tail[:] = chunk[-tail_limit:]
                    continue
                overflow = len(output_tail) + len(chunk) - tail_limit
                if overflow > 0:
                    del output_tail[:overflow]
                output_tail.extend(chunk)
        finally:
            _close_exec_stream(output_stream)

        inspected = api_client.exec_inspect(exec_id)
        output_truncated = output_size > _INLINE_OUTPUT_BYTES
        output_bytes = bytes(output_head)
        if output_truncated:
            output_bytes += _OUTPUT_TRUNCATION_MARKER
        output_bytes += output_tail
        output = output_bytes.decode("utf-8", errors="replace")
        return ExecuteResponse(
            output=self._sanitize_output(output) or "",
            exit_code=inspected.get("ExitCode"),
            truncated=output_truncated,
        )

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """在用户容器的当前会话目录中执行命令。"""
        with self._operation():
            return self._execute_unlocked(command, timeout=timeout)

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """异步执行命令并支持取消容量等待。"""
        return await self._run_async(lambda: self.execute(command, timeout=timeout))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读取当前会话文件。"""
        with self._resolved_operation(file_path) as resolved_path:
            if resolved_path is None:
                return ReadResult(error=INVALID_PATH)
            result = super().read(resolved_path, offset, limit)
            result.error = self._sanitize_output(result.error)
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
            preflight_error = self._write_preflight(resolved_path)
            if preflight_error is not None:
                preflight_error.error = self._sanitize_output(preflight_error.error)
                return preflight_error
            response = self.upload_fileobj(
                resolved_path,
                io.BytesIO(content.encode()),
            )
            if response.error:
                return WriteResult(
                    error=f"写入文件 '{file_path}' 失败: {response.error}"
                )
            return WriteResult(path=resolved_path)

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
            result = self._edit_file(
                resolved_path,
                old_string,
                new_string,
                replace_all,
            )
            return EditResult(
                error=self._sanitize_output(result.error),
                path=result.path,
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
        old_path = f"{self._workspace_dir}/.deepagents_tmp/{token}.old"
        new_path = f"{self._workspace_dir}/.deepagents_tmp/{token}.new"
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

    def _put_archive(self, path: str, content: BinaryIO, size: int) -> None:
        """先写入受保护的暂存目录，再提交到当前可写根。"""
        relative_target = posixpath.relpath(path, self._workspace_dir)
        if relative_target == "." or relative_target.startswith("../"):
            raise SandboxPathError(path)
        staging_name = f"upload-{secrets.token_hex(20)}"
        staging_path = posixpath.join(self._staging_dir, staging_name)
        try:
            # Docker put_archive 只能以守护进程权限写入；先落到不可预测的暂存名，
            # 再由受控脚本校验目录属主并原子替换目标文件。
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
            # 提交失败也必须清理 root 暂存文件，避免绕过工作区配额长期累积。
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
        *,
        from_end: bool = False,
    ) -> tuple[bytes, int | None]:
        """以会话 UID 限长读取文件开头或结尾。"""
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
                "tail" if from_end else "head",
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

    def upload_fileobj(self, path: str, content: BinaryIO) -> FileUploadResponse:
        """上传文件对象到当前会话。"""
        try:
            resolved_path = self._resolve_mutation_path(path)
            with self._operation():
                content.seek(0, io.SEEK_END)
                size = content.tell()
                content.seek(0)
                if size > self._max_file_bytes:
                    return FileUploadResponse(
                        path=path,
                        error=f"file_too_large:{self._max_file_bytes}",
                    )
                self._put_archive(resolved_path, content, size)
        except SandboxPathError:
            return FileUploadResponse(path=path, error=INVALID_PATH)
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
                    # stat 与读取之间文件可能变化，读取后再次校验长度才能守住上限。
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
