"""DeepAgents Docker 沙箱 Backend。"""

from __future__ import annotations

import base64
import io
import json
import posixpath
import secrets
import shlex
import tarfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import BinaryIO

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

from app.sandbox.contracts import SANDBOX_DATA_ROOT
from app.sandbox.docker_stream import close_exec_stream
from app.sandbox.errors import SandboxPathError
from app.sandbox.execution import SandboxExecution
from app.sandbox.paths import resolve_sandbox_path
from app.sandbox.scripts import (
    _COMMIT_UPLOAD_SCRIPT,
    _LARGE_EDIT_SCRIPT,
)
from app.sandbox.shell_runner import DockerShellJobRunner

_INLINE_OUTPUT_BYTES = 80_000
_OUTPUT_TRUNCATION_MARKER = b"\n...[middle output truncated]...\n"


class DockerSandboxBackend(BaseSandbox):
    """在一个用户容器中执行受 Conversation 和 Session 隔离的操作。"""

    def __init__(self, execution: SandboxExecution) -> None:
        """绑定工作区执行上下文并创建 Shell 任务执行器。"""
        self._execution = execution
        self.shell_jobs = DockerShellJobRunner(execution)

    @property
    def id(self) -> str:
        """返回绑定工作区的唯一标识。"""
        return self._execution.id

    @property
    def workspace_dir(self) -> str:
        """获取会话在容器中的实际工作目录。"""
        return self._execution.workspace_dir

    @property
    def conversation_dir(self) -> str:
        """获取当前 Conversation 在容器中的实际根目录。"""
        return self._execution.conversation_dir

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """在用户容器的当前会话目录中执行命令。"""
        with self._execution.operation():
            return self._execute_unlocked(command, timeout=timeout)

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """异步执行命令并支持取消容量等待。"""
        return await self._execution.run_async(
            lambda: self.execute(command, timeout=timeout)
        )

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读取当前会话文件。"""
        with self._resolved_operation(file_path) as resolved_path:
            if resolved_path is None:
                return ReadResult(error=INVALID_PATH)
            result = super().read(resolved_path, offset, limit)
            result.error = self._execution.sanitize_output(result.error)
            return result

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """异步读取当前会话文件。"""
        return await self._execution.run_async(
            lambda: self.read(file_path, offset, limit)
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        """写入当前会话文件。"""
        with self._resolved_operation(file_path, mutation=True) as resolved_path:
            if resolved_path is None:
                return WriteResult(error=INVALID_PATH)
            preflight_error = self._write_preflight(resolved_path)
            if preflight_error is not None:
                preflight_error.error = self._execution.sanitize_output(
                    preflight_error.error
                )
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
        return await self._execution.run_async(lambda: self.write(file_path, content))

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
                error=self._execution.sanitize_output(result.error),
                path=result.path,
                occurrences=result.occurrences,
            )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """异步编辑当前会话文件。"""
        return await self._execution.run_async(
            lambda: self.edit(
                file_path,
                old_string,
                new_string,
                replace_all,
            )
        )

    def upload_fileobj(self, path: str, content: BinaryIO) -> FileUploadResponse:
        """上传文件对象到当前会话。"""
        try:
            resolved_path = self._resolve_mutation_path(path)
            with self._execution.operation():
                content.seek(0, io.SEEK_END)
                size = content.tell()
                content.seek(0)
                if size > self._execution.max_file_bytes:
                    return FileUploadResponse(
                        path=path,
                        error=f"file_too_large:{self._execution.max_file_bytes}",
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
        return await self._execution.run_async(lambda: self.upload_files(files))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载当前会话文件。"""
        responses: list[FileDownloadResponse] = []
        with self._execution.operation():
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
                    if size > self._execution.max_file_bytes:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=f"file_too_large:{self._execution.max_file_bytes}",
                            )
                        )
                        continue
                    # stat 与读取之间文件可能变化，读取后再次校验长度才能守住上限。
                    content, exit_code = self._execution.read_file_bytes(
                        resolved_path, self._execution.max_file_bytes + 1
                    )
                    if len(content) > self._execution.max_file_bytes:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=f"file_too_large:{self._execution.max_file_bytes}",
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
        return await self._execution.run_async(lambda: self.download_files(paths))

    def _resolve_path(self, path: str) -> str:
        """按 execute 的工作目录语义解析文件工具路径。"""
        return resolve_sandbox_path(path, self._execution.workspace_dir)

    def _resolve_mutation_path(self, path: str) -> str:
        """只允许文件工具修改自身工作目录。"""
        return resolve_sandbox_path(
            path,
            self._execution.workspace_dir,
            allowed_root=self._execution.workspace_dir,
        )

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
        with self._execution.operation():
            yield resolved_path

    def _execute_unlocked(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """流式执行命令并限制宿主机保留的输出。"""
        effective_timeout = self._execution.internal_command_timeout_seconds
        if timeout is not None and timeout > 0:
            effective_timeout = min(
                timeout, self._execution.internal_command_timeout_seconds
            )
        file_limit_blocks = max(1, self._execution.max_file_bytes // 512)
        command_shell = (
            f"umask {self._execution.umask:03o}; ulimit -f {file_limit_blocks}; "
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

        docker_client = self._execution.container.client
        if docker_client is None:
            raise RuntimeError("Docker 容器客户端不可用")
        api_client = docker_client.api
        created = api_client.exec_create(
            self._execution.container.id,
            shell_command,
            stdout=True,
            stderr=True,
            user=f"{self._execution.execution_uid}:{self._execution.execution_gid}",
            environment={
                "HOME": f"{self._execution.workspace_dir}/.home",
                "UV_CACHE_DIR": f"{self._execution.workspace_dir}/.cache/uv",
                "XDG_CACHE_HOME": f"{self._execution.workspace_dir}/.cache",
                "TMPDIR": f"{self._execution.workspace_dir}/.tmp",
                "TMP": f"{self._execution.workspace_dir}/.tmp",
                "TEMP": f"{self._execution.workspace_dir}/.tmp",
            },
            workdir=self._execution.workspace_dir,
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
            close_exec_stream(output_stream)

        inspected = api_client.exec_inspect(exec_id)
        output_truncated = output_size > _INLINE_OUTPUT_BYTES
        output_bytes = bytes(output_head)
        if output_truncated:
            output_bytes += _OUTPUT_TRUNCATION_MARKER
        output_bytes += output_tail
        output = output_bytes.decode("utf-8", errors="replace")
        return ExecuteResponse(
            output=self._execution.sanitize_output(output) or "",
            exit_code=inspected.get("ExitCode"),
            truncated=output_truncated,
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
        old_path = f"{self._execution.workspace_dir}/.deepagents_tmp/{token}.old"
        new_path = f"{self._execution.workspace_dir}/.deepagents_tmp/{token}.new"
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
                    "workspace": self._execution.workspace_dir,
                    "max_file_bytes": self._execution.max_file_bytes,
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

    def _put_archive(self, path: str, content: BinaryIO, size: int) -> None:
        """先写入受保护的暂存目录，再提交到当前可写根。"""
        relative_target = posixpath.relpath(path, self._execution.workspace_dir)
        if relative_target == "." or relative_target.startswith("../"):
            raise SandboxPathError(path)
        staging_name = f"upload-{secrets.token_hex(20)}"
        staging_path = posixpath.join(self._execution.staging_dir, staging_name)
        try:
            # Docker put_archive 只能以守护进程权限写入；先落到不可预测的暂存名，
            # 再由受控脚本校验目录属主并原子替换目标文件。
            with io.BytesIO() as archive_buffer:
                with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
                    info = tarfile.TarInfo(name=staging_name)
                    info.mtime = int(time.time())
                    info.size = size
                    info.mode = 0o600
                    info.uid = 0
                    info.gid = 0
                    archive.addfile(info, content)
                archive_buffer.seek(0)
                if not self._execution.container.put_archive(
                    self._execution.staging_dir, archive_buffer
                ):
                    raise OSError(f"暂存上传文件失败: {path}")

            payload = base64.b64encode(
                json.dumps(
                    {
                        "root": self._execution.workspace_dir,
                        "source": staging_path,
                        "owner_uid": self._execution.execution_uid,
                        "owner_gid": self._execution.execution_gid,
                        "file_mode": self._execution.file_mode,
                        "directory_mode": self._execution.directory_mode,
                        "relative_target": relative_target,
                    }
                ).encode()
            ).decode()
            commit_result = self._execution.container.exec_run(
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
            self._execution.container.exec_run(
                ["rm", "-f", "--", staging_path],
                user="0",
                privileged=True,
                workdir=SANDBOX_DATA_ROOT,
            )
