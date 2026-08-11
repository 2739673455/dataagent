"""本地 Docker 沙盒管理"""

import asyncio
import base64
import hashlib
import io
import json
import os
import posixpath
import secrets
import shlex
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
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
from loguru import logger

import docker
from app.conf.app_config import ROOT_DIR, SandboxConfig, cfg

_CONTAINER_LABEL = "dataagent.sandbox.user_id"
_CONTAINER_PREFIX = "dataagent-sandbox-user"
_VOLUME_PREFIX = "dataagent-sandbox-user"
_SANDBOX_WORKSPACE_ROOT = "/workspace/conversations"
_DEFAULT_EXECUTE_TIMEOUT = 120
_MIN_CONVERSATION_UID = 100_000
_MAX_CONVERSATION_UID = 2_147_483_646
_CONTAINER_SPEC_LABEL = "dataagent.sandbox.spec"
_PATH_MAX_BYTES = 4096
_PATH_COMPONENT_MAX_BYTES = 255
_SANDBOX_STAGING_ROOT = "/workspace/.dataagent-staging"
_SANDBOX_UID_REGISTRY = "/workspace/.dataagent-uids.json"
_UID_REGISTRY_VERSION = 1
_ARCHIVE_SPOOL_BYTES = 8 * 1024 * 1024

_COMMIT_UPLOAD_SCRIPT = """
import base64
import json
import os
import stat
import sys

payload = json.loads(base64.b64decode(sys.argv[1]).decode())
root = payload["root"]
source = payload["source"]
owner_uid = int(payload["owner_uid"])
parts = payload["relative_target"].split("/")
source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
try:
    if not stat.S_ISREG(os.fstat(source_fd).st_mode):
        raise OSError("invalid staged file")
    os.fchown(source_fd, owner_uid, owner_uid)
    os.fchmod(source_fd, 0o600)
finally:
    os.close(source_fd)
root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
directory_fd = os.dup(root_fd)
try:
    if not stat.S_ISDIR(os.fstat(root_fd).st_mode) or os.fstat(root_fd).st_uid != owner_uid:
        raise PermissionError("invalid workspace owner")
    for component in parts[:-1]:
        created = False
        try:
            os.mkdir(component, mode=0o700, dir_fd=directory_fd)
            created = True
        except FileExistsError:
            pass
        next_fd = os.open(
            component,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        if created:
            os.fchown(next_fd, owner_uid, owner_uid)
        if os.fstat(next_fd).st_uid != owner_uid:
            raise PermissionError("invalid target directory owner")
        os.close(directory_fd)
        directory_fd = next_fd
    os.replace(
        source,
        parts[-1],
        src_dir_fd=None,
        dst_dir_fd=directory_fd,
    )
finally:
    os.close(directory_fd)
    os.close(root_fd)
""".strip()

_LARGE_EDIT_SCRIPT = """
import base64
import json
import os
import stat
import sys

payload = json.loads(base64.b64decode(sys.argv[1]).decode())
try:
    with open(payload["old"], "rb") as old_file:
        old = old_file.read().decode("utf-8")
    with open(payload["new"], "rb") as new_file:
        new = new_file.read().decode("utf-8")
    target_stat = os.stat(payload["target"])
    if not stat.S_ISREG(target_stat.st_mode):
        print(json.dumps({"error": "not_a_file"}))
        sys.exit(0)
    with open(payload["target"], "rb") as target_file:
        content = target_file.read().decode("utf-8")

    old_crlf = old.replace("\\r\\n", "\\n").replace("\\n", "\\r\\n")
    old_lf = old.replace("\\r\\n", "\\n")
    new_crlf = new.replace("\\r\\n", "\\n").replace("\\n", "\\r\\n")
    new_lf = new.replace("\\r\\n", "\\n")
    count = 0
    matched_old, matched_new = old, new
    for candidate_old, candidate_new in (
        (old, new),
        (old_crlf, new_crlf),
        (old_lf, new_lf),
    ):
        candidate_count = content.count(candidate_old)
        if candidate_count:
            matched_old = candidate_old
            matched_new = candidate_new
            count = candidate_count
            break
    if count == 0:
        print(json.dumps({"error": "string_not_found"}))
    elif count > 1 and not payload["replace_all"]:
        print(json.dumps({"error": "multiple_occurrences", "count": count}))
    else:
        updated = (
            content.replace(matched_old, matched_new)
            if payload["replace_all"]
            else content.replace(matched_old, matched_new, 1)
        )
        updated_bytes = updated.encode("utf-8")
        if len(updated_bytes) > payload["max_file_bytes"]:
            print(json.dumps({"error": "file_too_large"}))
            sys.exit(0)
        workspace_size = 0
        for current_root, _, files in os.walk(payload["workspace"]):
            for name in files:
                current_path = os.path.join(current_root, name)
                try:
                    if os.path.isfile(current_path) and not os.path.islink(current_path):
                        workspace_size += os.path.getsize(current_path)
                except OSError:
                    pass
        projected_size = len(updated_bytes) + workspace_size - len(content.encode("utf-8"))
        if projected_size > payload["max_workspace_bytes"]:
            print(json.dumps({"error": "workspace_limit_exceeded"}))
            sys.exit(0)
        with open(payload["target"], "wb") as target_file:
            target_file.write(updated_bytes)
        print(json.dumps({"count": count}))
except FileNotFoundError:
    print(json.dumps({"error": "file_not_found"}))
except PermissionError:
    print(json.dumps({"error": "permission_denied"}))
except UnicodeDecodeError:
    print(json.dumps({"error": "not_a_text_file"}))
finally:
    for path in (payload["old"], payload["new"]):
        try:
            os.remove(path)
        except OSError:
            pass
""".strip()


class SandboxPathError(ValueError):
    """沙盒路径非法"""


class SandboxFileTooLargeError(OSError):
    """沙盒文件超过大小限制"""


class SandboxStorageLimitError(OSError):
    """沙盒工作区超过容量限制"""


class SandboxDeletedError(RuntimeError):
    """沙盒资源已被删除"""


class _IteratorReader(io.RawIOBase):
    """将 Docker archive 字节迭代器适配为 tarfile 可读取的流"""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        super().__init__()
        self._chunks = chunks
        self._buffer = bytearray()
        self._finished = False

    def readable(self) -> bool:
        """声明流支持读取"""
        return True

    def readinto(self, target: Any) -> int:
        """按需从 Docker 响应流填充目标缓冲区"""
        if self.closed:
            return 0
        view = memoryview(target).cast("B")
        while len(self._buffer) < len(view) and not self._finished:
            try:
                self._buffer.extend(next(self._chunks))
            except StopIteration:
                self._finished = True
        size = min(len(view), len(self._buffer))
        view[:size] = self._buffer[:size]
        del self._buffer[:size]
        return size

    def close(self) -> None:
        """关闭底层 Docker 响应流"""
        close_chunks = getattr(self._chunks, "close", None)
        if callable(close_chunks):
            close_chunks()
        super().close()


class _LifecycleGuard:
    """协调并发操作与资源维护"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_operations = 0
        self._maintenance = False
        self._deleted = False
        self._local = threading.local()

    @contextmanager
    def operation(self) -> Iterator[None]:
        """进入可并发执行的资源操作"""
        operation_depth = getattr(self._local, "operation_depth", 0)
        maintenance_depth = getattr(self._local, "maintenance_depth", 0)
        if operation_depth or maintenance_depth:
            self._local.operation_depth = operation_depth + 1
            try:
                yield
            finally:
                self._local.operation_depth -= 1
            return

        with self._condition:
            while self._maintenance:
                self._condition.wait()
            if self._deleted:
                raise SandboxDeletedError("sandbox resource has been deleted")
            self._active_operations += 1
        self._local.operation_depth = 1
        try:
            yield
        finally:
            self._local.operation_depth = 0
            with self._condition:
                self._active_operations -= 1
                self._condition.notify_all()

    @contextmanager
    def maintenance(self) -> Iterator[None]:
        """独占资源并等待现有操作完成"""
        if getattr(self._local, "operation_depth", 0):
            raise RuntimeError("maintenance cannot start inside an operation")
        with self._condition:
            while self._maintenance:
                self._condition.wait()
            self._maintenance = True
            while self._active_operations:
                self._condition.wait()
        self._local.maintenance_depth = 1
        try:
            yield
        finally:
            self._local.maintenance_depth = 0
            with self._condition:
                self._maintenance = False
                self._condition.notify_all()

    @contextmanager
    def try_maintenance(self) -> Iterator[bool]:
        """仅在当前没有操作时尝试获取独占维护权"""
        if getattr(self._local, "operation_depth", 0):
            yield False
            return
        with self._condition:
            if self._maintenance or self._active_operations or self._deleted:
                yield False
                return
            self._maintenance = True
        self._local.maintenance_depth = 1
        try:
            yield True
        finally:
            self._local.maintenance_depth = 0
            with self._condition:
                self._maintenance = False
                self._condition.notify_all()

    @property
    def active_operations(self) -> int:
        """获取当前活跃操作数"""
        with self._condition:
            return self._active_operations

    def mark_deleted(self) -> None:
        """阻止资源继续接受新操作"""
        with self._condition:
            self._deleted = True
            self._condition.notify_all()


def normalize_attachment_path(path: str) -> str:
    """校验并规范化会话内的附件相对路径"""
    encoded_path = path.encode("utf-8", errors="surrogatepass")
    if (
        not path
        or path.startswith(("/", "~"))
        or "\\" in path
        or any(character == "\x7f" or ord(character) < 32 for character in path)
        or len(encoded_path) > _PATH_MAX_BYTES
    ):
        raise SandboxPathError(path)
    parts = PurePosixPath(path).parts
    if not parts or any(
        part in {"", ".", ".."}
        or len(part.encode("utf-8", errors="surrogatepass")) > _PATH_COMPONENT_MAX_BYTES
        for part in parts
    ):
        raise SandboxPathError(path)
    return PurePosixPath(*parts).as_posix()


class DockerSandboxBackend(BaseSandbox):
    """将一个用户容器中的会话目录暴露为虚拟文件系统"""

    enable_capture_offload = True

    def __init__(
        self,
        user_id: int,
        conversation_id: UUID,
        conversation_uid: int,
        sandbox_config: SandboxConfig,
        user_guard: _LifecycleGuard,
        conversation_guard: _LifecycleGuard,
        mutation_lock: threading.RLock,
        touch: Callable[[], None],
        get_running_container: Callable[[], Container],
    ) -> None:
        """初始化会话级 Docker 沙盒后端"""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._workspace_dir = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        self._conversation_uid = conversation_uid
        self._max_output_bytes = sandbox_config.max_output_bytes
        self._max_capture_bytes = sandbox_config.max_capture_bytes
        self._max_file_bytes = sandbox_config.max_file_bytes
        self._max_workspace_bytes = sandbox_config.max_workspace_bytes
        self._user_guard = user_guard
        self._conversation_guard = conversation_guard
        self._mutation_lock = mutation_lock
        self._touch = touch
        self._get_running_container = get_running_container
        self._operation_local = threading.local()

    @property
    def _container(self) -> Container:
        """获取当前操作持有的容器实例"""
        container = getattr(self._operation_local, "container", None)
        if container is None:
            raise RuntimeError("Docker container is only available during an operation")
        return container

    @property
    def id(self) -> str:
        """获取沙盒后端唯一标识"""
        return f"docker:{self._user_id}:{self._conversation_id}"

    @property
    def workspace_dir(self) -> str:
        """获取会话在容器中的实际工作目录"""
        return self._workspace_dir

    def _resolve_path(self, path: str) -> str:
        """将虚拟路径映射到当前会话目录"""
        if "\x00" in path or path.startswith("~"):
            raise SandboxPathError(path)

        if path == self._workspace_dir or path.startswith(f"{self._workspace_dir}/"):
            return path

        parts = PurePosixPath(path).parts
        if any(part == ".." for part in parts):
            raise SandboxPathError(path)
        relative_parts = parts[1:] if PurePosixPath(path).is_absolute() else parts
        return posixpath.join(self._workspace_dir, *relative_parts)

    def _to_virtual_path(self, path: str) -> str:
        """将容器路径还原为 Agent 可见的虚拟路径"""
        if path == self._workspace_dir:
            return "/"
        prefix = f"{self._workspace_dir}/"
        if path.startswith(prefix):
            return f"/{path[len(prefix) :]}"
        if not path.startswith("/"):
            normalized_path = PurePosixPath(path).as_posix()
            return f"/{normalized_path}" if normalized_path != "." else "/"
        return path

    def _hide_workspace(self, message: str | None) -> str | None:
        """从错误信息中隐藏容器工作目录"""
        if message is None:
            return None
        return message.replace(self._workspace_dir, "")

    def _map_file_info(self, info: FileInfo) -> FileInfo:
        """转换文件信息中的路径"""
        return FileInfo(**{**info, "path": self._to_virtual_path(info["path"])})

    def _map_grep_match(self, match: GrepMatch) -> GrepMatch:
        """转换搜索结果中的路径"""
        return GrepMatch(**{**match, "path": self._to_virtual_path(match["path"])})

    @contextmanager
    def _operation(self) -> Iterator[None]:
        """在资源生命周期保护下执行沙盒操作"""
        self._touch()
        existing_container = getattr(self._operation_local, "container", None)
        try:
            with self._user_guard.operation():
                if existing_container is None:
                    self._operation_local.container = self._get_running_container()
                with self._conversation_guard.operation():
                    yield
        finally:
            if existing_container is None and hasattr(
                self._operation_local, "container"
            ):
                del self._operation_local.container
            self._touch()

    def _execute_unlocked(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """流式执行命令并限制宿主机保留的输出"""
        effective_timeout = _DEFAULT_EXECUTE_TIMEOUT if timeout is None else timeout
        file_limit_blocks = max(1, self._max_file_bytes // 512)
        command_shell = (
            f"umask 077; ulimit -f {file_limit_blocks}; "
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
            raise RuntimeError("Docker container client is unavailable")
        api_client = docker_client.api
        created = api_client.exec_create(
            self._container.id,
            shell_command,
            stdout=True,
            stderr=True,
            user=f"{self._conversation_uid}:{self._conversation_uid}",
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
        output_tail = bytearray()
        output_size = 0
        output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
        try:
            for chunk in output_stream:
                if isinstance(chunk, tuple):
                    chunk = b"".join(item for item in chunk if isinstance(item, bytes))
                elif isinstance(chunk, int):
                    chunk = bytes([chunk])
                elif isinstance(chunk, str):
                    chunk = chunk.encode()
                output_size += len(chunk)
                output_tail.extend(chunk)
                if len(output_tail) > self._max_output_bytes:
                    del output_tail[: len(output_tail) - self._max_output_bytes]
        finally:
            close_stream = getattr(output_stream, "close", None)
            if callable(close_stream):
                close_stream()
            stream_response = getattr(output_stream, "_response", None)
            if stream_response is not None:
                stream_response.close()

        inspected = api_client.exec_inspect(exec_id)
        return ExecuteResponse(
            output=output_tail.decode("utf-8", errors="replace"),
            exit_code=inspected.get("ExitCode"),
            truncated=output_size > self._max_output_bytes,
        )

    def _workspace_size_unlocked(self) -> int:
        """读取当前会话目录占用的字节数"""
        result = self._execute_unlocked("du -sb . | cut -f1")
        if result.exit_code != 0:
            raise OSError(result.output.strip() or "failed to inspect workspace size")
        try:
            return int(result.output.strip())
        except ValueError as exc:
            raise OSError("invalid workspace size response") from exc

    def _validate_workspace_capacity_unlocked(
        self,
        incoming_bytes: int,
        replaced_bytes: int = 0,
    ) -> None:
        """校验写入后工作区不会超过容量限制"""
        projected_size = (
            self._workspace_size_unlocked() - replaced_bytes + incoming_bytes
        )
        if projected_size > self._max_workspace_bytes:
            raise SandboxStorageLimitError(
                f"workspace limit exceeded: {projected_size} > {self._max_workspace_bytes}"
            )

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """在用户容器的当前会话目录中执行命令"""
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

    def execute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,
    ) -> ExecuteOffloadResult:
        """在会话目录中对大命令输出进行源端卸载"""
        capture_limit = min(
            max_capture_bytes or self._max_capture_bytes,
            self._max_capture_bytes,
        )
        return super().execute_with_offload(
            command,
            self._resolve_path(capture_path),
            max_inline_bytes=max_inline_bytes,
            max_capture_bytes=capture_limit,
            timeout=timeout,
        )

    def ls(self, path: str) -> LsResult:
        """列出当前会话目录内容"""
        try:
            resolved_path = self._resolve_path(path)
        except SandboxPathError:
            return LsResult(error=INVALID_PATH)
        with self._operation():
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
        """异步列出当前会话目录内容"""
        return await asyncio.to_thread(self.ls, path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读取当前会话文件"""
        try:
            resolved_path = self._resolve_path(file_path)
        except SandboxPathError:
            return ReadResult(error=INVALID_PATH)
        with self._operation():
            result = super().read(resolved_path, offset, limit)
            result.error = self._hide_workspace(result.error)
            return result

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """异步读取当前会话文件"""
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        """写入当前会话文件"""
        try:
            resolved_path = self._resolve_path(file_path)
        except SandboxPathError:
            return WriteResult(error=INVALID_PATH)
        with self._operation():
            result = super().write(resolved_path, content)
            return WriteResult(
                error=self._hide_workspace(result.error),
                path=self._to_virtual_path(result.path) if result.path else None,
            )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """异步写入当前会话文件"""
        return await asyncio.to_thread(self.write, file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """编辑当前会话文件"""
        try:
            resolved_path = self._resolve_path(file_path)
        except SandboxPathError:
            return EditResult(error=INVALID_PATH)
        with self._operation():
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
        """通过会话目录内的临时文件安全编辑文本"""
        token = secrets.token_hex(10)
        old_path = self._resolve_path(f"/.deepagents_tmp/{token}.old")
        new_path = self._resolve_path(f"/.deepagents_tmp/{token}.new")
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
            return EditResult(error=f"Error editing file '{file_path}': {error}")

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
            detail = result.output.strip() or "unknown error"
            return EditResult(error=f"Error editing file '{file_path}': {detail}")
        if error := response.get("error"):
            return EditResult(error=f"Error editing file '{file_path}': {error}")
        return EditResult(path=file_path, occurrences=response.get("count", 1))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """异步编辑当前会话文件"""
        return await asyncio.to_thread(
            self.edit,
            file_path,
            old_string,
            new_string,
            replace_all,
        )

    def delete(self, file_path: str) -> DeleteResult:
        """删除当前会话文件或目录"""
        try:
            resolved_path = self._resolve_path(file_path)
        except SandboxPathError:
            return DeleteResult(error=INVALID_PATH)
        with self._operation():
            with self._mutation_lock:
                result = super().delete(resolved_path)
            return DeleteResult(
                error=self._hide_workspace(result.error),
                path=self._to_virtual_path(result.path) if result.path else None,
            )

    async def adelete(self, file_path: str) -> DeleteResult:
        """异步删除当前会话文件或目录"""
        return await asyncio.to_thread(self.delete, file_path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """搜索当前会话文件内容"""
        try:
            resolved_path = self._resolve_path(path or "/")
        except SandboxPathError:
            return GrepResult(error=INVALID_PATH)
        with self._operation():
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
        """异步搜索当前会话文件内容"""
        return await asyncio.to_thread(
            self.grep,
            pattern,
            path,
            glob,
            max_count=max_count,
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """匹配当前会话中的文件"""
        try:
            resolved_path = self._resolve_path(path or "/")
        except SandboxPathError:
            return GlobResult(error=INVALID_PATH)
        with self._operation():
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
        """异步匹配当前会话中的文件"""
        return await asyncio.to_thread(self.glob, pattern, path)

    def _put_archive(self, path: str, content: BinaryIO, size: int) -> None:
        """先写入受保护的暂存目录，再由会话 UID 安全提交文件"""
        relative_target = posixpath.relpath(path, self._workspace_dir)
        if relative_target == "." or relative_target.startswith("../"):
            raise SandboxPathError(path)
        staging_dir = posixpath.join(
            _SANDBOX_STAGING_ROOT,
            PurePosixPath(self._workspace_dir).name,
        )
        staging_name = f"upload-{secrets.token_hex(20)}"
        staging_path = posixpath.join(staging_dir, staging_name)
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
                if not self._container.put_archive(staging_dir, archive_buffer):
                    raise OSError(f"Failed to stage uploaded file: {path}")

            payload = base64.b64encode(
                json.dumps(
                    {
                        "root": self._workspace_dir,
                        "source": staging_path,
                        "owner_uid": self._conversation_uid,
                        "relative_target": relative_target,
                    }
                ).encode()
            ).decode()
            commit_result = self._container.exec_run(
                ["python3", "-c", _COMMIT_UPLOAD_SCRIPT, payload],
                user="0",
                privileged=True,
                workdir="/workspace",
            )
            if commit_result.exit_code != 0:
                raw_output = commit_result.output or b""
                detail = (
                    raw_output.decode("utf-8", errors="replace")
                    if isinstance(raw_output, bytes)
                    else str(raw_output)
                ).strip()
                raise OSError(f"Failed to commit uploaded file: {detail}")
        finally:
            self._container.exec_run(
                ["rm", "-f", "--", staging_path],
                user="0",
                privileged=True,
                workdir="/workspace",
            )

    def _read_file_bytes_unlocked(self, path: str) -> tuple[bytes, int | None]:
        """以会话 UID 限长读取文件，避免 Docker 守护进程绕过权限"""
        docker_client = self._container.client
        if docker_client is None:
            raise RuntimeError("Docker container client is unavailable")
        api_client = docker_client.api
        created = api_client.exec_create(
            self._container.id,
            [
                "timeout",
                "--signal=KILL",
                str(_DEFAULT_EXECUTE_TIMEOUT),
                "head",
                "-c",
                str(self._max_file_bytes + 1),
                "--",
                path,
            ],
            stdout=True,
            stderr=True,
            user=f"{self._conversation_uid}:{self._conversation_uid}",
            environment={"HOME": f"{self._workspace_dir}/.home"},
            workdir=self._workspace_dir,
        )
        exec_id = created["Id"]
        output = bytearray()
        output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
        try:
            for chunk in output_stream:
                if isinstance(chunk, tuple):
                    output.extend(
                        b"".join(item for item in chunk if isinstance(item, bytes))
                    )
                elif isinstance(chunk, int):
                    output.append(chunk)
                elif isinstance(chunk, str):
                    output.extend(chunk.encode())
                else:
                    output.extend(chunk)
        finally:
            close_stream = getattr(output_stream, "close", None)
            if callable(close_stream):
                close_stream()
            stream_response = getattr(output_stream, "_response", None)
            if stream_response is not None:
                stream_response.close()
        inspected = api_client.exec_inspect(exec_id)
        return bytes(output), inspected.get("ExitCode")

    def _file_size_unlocked(self, path: str) -> int:
        """读取文件字节数，不存在时返回零"""
        result = self._execute_unlocked(
            f"if [ -f {shlex.quote(path)} ]; then stat -c %s -- {shlex.quote(path)}; else printf 0; fi"
        )
        if result.exit_code != 0:
            raise OSError(result.output.strip() or f"Failed to inspect file: {path}")
        try:
            return int(result.output.strip())
        except ValueError as exc:
            raise OSError(f"Invalid file size response: {path}") from exc

    def upload_fileobj(self, path: str, content: BinaryIO) -> FileUploadResponse:
        """上传文件对象到当前会话"""
        try:
            resolved_path = self._resolve_path(path)
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
        """批量上传字节内容到当前会话"""
        return [
            self.upload_fileobj(path, io.BytesIO(content)) for path, content in files
        ]

    async def aupload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        """异步批量上传字节内容到当前会话"""
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载当前会话文件"""
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
        """异步批量下载当前会话文件"""
        return await asyncio.to_thread(self.download_files, paths)

    def is_file(self, path: str) -> bool:
        """检查当前会话路径是否为文件"""
        resolved_path = self._resolve_path(path)
        with self._operation():
            result = self._execute_unlocked(f"test -f {shlex.quote(resolved_path)}")
            return result.exit_code == 0


class DockerSandboxManager:
    """管理每个用户唯一的本地 Docker 沙盒"""

    def __init__(self, sandbox_config: SandboxConfig) -> None:
        """初始化 Docker 沙盒管理器"""
        self._config = sandbox_config
        self._client: docker.DockerClient | None = None
        self._container_spec: str | None = None
        self._init_lock = asyncio.Lock()
        self._resource_lock = threading.RLock()
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._user_guards: dict[int, _LifecycleGuard] = {}
        self._conversation_guards: dict[tuple[int, UUID], _LifecycleGuard] = {}
        self._mutation_locks: dict[tuple[int, UUID], threading.RLock] = {}
        self._start_locks: dict[int, threading.Lock] = {}
        self._activity_lock = threading.Lock()
        self._last_activity: dict[int, float] = {}
        self._capacity_condition = threading.Condition()
        self._running_users: set[int] = set()
        self._reserved_users: set[int] = set()
        self._cleanup_task: asyncio.Task[None] | None = None

    def _get_client(self) -> docker.DockerClient:
        """获取已初始化的 Docker 客户端"""
        if self._client is None:
            raise RuntimeError("Docker sandbox manager is not initialized")
        return self._client

    def _init_sync(self) -> None:
        """连接 Docker 并确保沙盒镜像存在"""
        client = docker.from_env()
        try:
            client.ping()
            build_context = Path(self._config.build_context)
            if not build_context.is_absolute():
                build_context = ROOT_DIR / build_context
            if self._config.rebuild_image:
                logger.info(f"Build Docker sandbox image: image={self._config.image}")
                proxy_build_args = {
                    name: value
                    for name in (
                        "HTTP_PROXY",
                        "HTTPS_PROXY",
                        "NO_PROXY",
                        "http_proxy",
                        "https_proxy",
                        "no_proxy",
                    )
                    if (value := os.getenv(name))
                }
                proxy_build_args.update(
                    {
                        "NODE_VERSION": self._config.node_version,
                        "NODE_DOWNLOAD_BASE": self._config.node_download_base,
                        "PYPI_INDEX_URL": self._config.pypi_index_url,
                        "NPM_REGISTRY": self._config.npm_registry,
                    }
                )
                image, _ = client.images.build(
                    path=str(build_context),
                    tag=self._config.image,
                    rm=True,
                    network_mode=self._config.build_network_mode,
                    buildargs=proxy_build_args,
                )
            else:
                image = client.images.get(self._config.image)
            spec_payload = {
                "layout_version": 3,
                "image_id": image.id,
                "memory_limit": self._config.memory_limit,
                "nano_cpus": self._config.nano_cpus,
                "pids_limit": self._config.pids_limit,
                "network_mode": self._config.network_mode,
            }
            self._container_spec = hashlib.sha256(
                json.dumps(spec_payload, sort_keys=True).encode()
            ).hexdigest()
        except Exception:
            client.close()
            raise
        self._client = client

    async def init(self) -> None:
        """初始化 Docker 沙盒管理器"""
        async with self._init_lock:
            if self._client is None:
                await asyncio.to_thread(self._init_sync)
                await asyncio.to_thread(self._reconcile_running_containers_sync)
            if self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(
                    self._cleanup_idle_containers()
                )

    async def _get_resources(
        self,
        user_id: int,
        conversation_id: UUID | None = None,
    ) -> tuple[
        asyncio.Lock,
        _LifecycleGuard,
        threading.Lock,
        _LifecycleGuard | None,
        threading.RLock | None,
    ]:
        """获取用户和会话的并发控制资源"""
        with self._resource_lock:
            user_lock = self._user_locks.setdefault(user_id, asyncio.Lock())
            user_guard = self._user_guards.setdefault(user_id, _LifecycleGuard())
            start_lock = self._start_locks.setdefault(user_id, threading.Lock())
            conversation_guard = None
            mutation_lock = None
            if conversation_id is not None:
                conversation_guard = self._conversation_guards.setdefault(
                    (user_id, conversation_id),
                    _LifecycleGuard(),
                )
                mutation_lock = self._mutation_locks.setdefault(
                    (user_id, conversation_id),
                    threading.RLock(),
                )
            return (
                user_lock,
                user_guard,
                start_lock,
                conversation_guard,
                mutation_lock,
            )

    def _touch_user(self, user_id: int) -> None:
        """记录用户沙盒最近活动时间"""
        with self._activity_lock:
            self._last_activity[user_id] = time.time()
        with self._capacity_condition:
            self._capacity_condition.notify_all()

    def _idle_seconds(self, user_id: int) -> float:
        """获取用户沙盒持续空闲的秒数"""
        with self._activity_lock:
            last_activity = self._last_activity.get(user_id)
        if last_activity is None:
            return 0.0
        return max(0.0, time.time() - last_activity)

    def _container_name(self, user_id: int) -> str:
        """构造用户容器名称"""
        return f"{_CONTAINER_PREFIX}-{user_id}"

    def _volume_name(self, user_id: int) -> str:
        """构造用户数据卷名称"""
        return f"{_VOLUME_PREFIX}-{user_id}-data"

    def _get_or_create_volume(self, user_id: int):
        """获取用户数据卷并校验归属"""
        client = self._get_client()
        volume_name = self._volume_name(user_id)
        try:
            volume = client.volumes.get(volume_name)
        except NotFound:
            return client.volumes.create(
                name=volume_name,
                labels={_CONTAINER_LABEL: str(user_id)},
            )
        volume.reload()
        if volume.attrs.get("Labels", {}).get(_CONTAINER_LABEL) != str(user_id):
            raise RuntimeError(f"Docker volume name is already in use: {volume_name}")
        return volume

    def _create_container(self, user_id: int) -> Container:
        """创建保持停止状态的用户容器"""
        client = self._get_client()
        volume = self._get_or_create_volume(user_id)
        if self._container_spec is None:
            raise RuntimeError("Docker sandbox container spec is unavailable")

        container = client.containers.create(
            self._config.image,
            name=self._container_name(user_id),
            command=["sleep", "infinity"],
            init=True,
            read_only=True,
            user="1000:1000",
            working_dir="/workspace",
            volumes={volume.name: {"bind": "/workspace", "mode": "rw"}},
            tmpfs={"/tmp": "rw,nosuid,nodev,size=256m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=self._config.memory_limit,
            nano_cpus=self._config.nano_cpus,
            pids_limit=self._config.pids_limit,
            network_mode=self._config.network_mode,
            environment={
                "HOME": "/tmp",
            },
            labels={
                _CONTAINER_LABEL: str(user_id),
                _CONTAINER_SPEC_LABEL: self._container_spec,
            },
        )
        logger.info(f"Create stopped user Docker sandbox: user_id={user_id}")
        return container

    def _get_or_create_storage_container_sync(self, user_id: int) -> Container:
        """获取或创建容器，但不启动容器"""
        if user_id < 0:
            raise ValueError("user_id must be non-negative")
        name = self._container_name(user_id)
        container = self._get_existing_container_sync(user_id)
        if container is not None:
            if container.labels.get(_CONTAINER_SPEC_LABEL) == self._container_spec:
                return container
            logger.info(f"Recreate outdated Docker sandbox: user_id={user_id}")
            container.remove(force=True)
            self._mark_user_not_running(user_id)
        try:
            return self._create_container(user_id)
        except APIError as exc:
            if exc.status_code != 409:
                raise
            existing_container = self._get_existing_container_sync(user_id)
            if existing_container is None:
                raise RuntimeError(f"Docker container creation raced: {name}") from exc
            return existing_container

    def _get_existing_container_sync(self, user_id: int) -> Container | None:
        """获取已存在的用户容器"""
        name = self._container_name(user_id)
        try:
            container = self._get_client().containers.get(name)
        except NotFound:
            return None
        container.reload()
        if container.labels.get(_CONTAINER_LABEL) != str(user_id):
            raise RuntimeError(f"Docker container name is already in use: {name}")
        return container

    def _mark_user_not_running(self, user_id: int) -> None:
        """释放用户占用的全局运行槽位"""
        with self._capacity_condition:
            self._running_users.discard(user_id)
            self._reserved_users.discard(user_id)
            self._capacity_condition.notify_all()

    def _complete_running_reservation(
        self,
        user_id: int,
        *,
        running: bool,
    ) -> None:
        """完成或回滚运行槽位预留"""
        with self._capacity_condition:
            self._reserved_users.discard(user_id)
            if running:
                self._running_users.add(user_id)
            else:
                self._running_users.discard(user_id)
            self._capacity_condition.notify_all()

    def _try_evict_idle_user_sync(self, user_id: int) -> bool:
        """尝试停止一个没有活跃操作的用户容器"""
        with self._resource_lock:
            user_guard = self._user_guards.setdefault(user_id, _LifecycleGuard())
            start_lock = self._start_locks.setdefault(user_id, threading.Lock())
        with user_guard.try_maintenance() as acquired:
            if not acquired:
                return False
            with start_lock:
                container = self._get_existing_container_sync(user_id)
                if container is not None and container.status == "running":
                    container.stop(timeout=10)
                    logger.info(
                        f"Stop LRU Docker sandbox for capacity: user_id={user_id}"
                    )
                self._mark_user_not_running(user_id)
                return True

    def _reserve_running_slot_sync(self, user_id: int) -> bool:
        """等待并预留一个全局运行容器槽位"""
        while True:
            with self._capacity_condition:
                if user_id in self._running_users:
                    return False
                if user_id in self._reserved_users:
                    self._capacity_condition.wait(timeout=0.5)
                    continue
                occupied = len(self._running_users) + len(self._reserved_users)
                if occupied < self._config.max_running_containers:
                    self._reserved_users.add(user_id)
                    return True
                with self._activity_lock:
                    last_activity = dict(self._last_activity)
                candidates = sorted(
                    self._running_users,
                    key=lambda candidate: last_activity.get(candidate, 0.0),
                )

            if any(
                self._try_evict_idle_user_sync(candidate) for candidate in candidates
            ):
                continue
            with self._capacity_condition:
                self._capacity_condition.wait(timeout=0.5)

    def _get_running_container_sync(
        self,
        user_id: int,
        start_lock: threading.Lock,
    ) -> Container:
        """获取用户容器并在取得全局槽位后按需启动"""
        with start_lock:
            container = self._get_or_create_storage_container_sync(user_id)
            container.reload()
            reserved = self._reserve_running_slot_sync(user_id)
            try:
                if container.status != "running":
                    container.start()
                    container.reload()
                    logger.info(f"Start Docker sandbox: user_id={user_id}")
            except Exception:
                if reserved:
                    self._complete_running_reservation(user_id, running=False)
                raise
            if reserved:
                self._complete_running_reservation(user_id, running=True)
            else:
                with self._capacity_condition:
                    self._running_users.add(user_id)
            return container

    def _reconcile_running_containers_sync(self) -> None:
        """启动时登记已有容器并收敛到运行上限"""
        containers = self._get_client().containers.list(
            all=True,
            filters={"label": _CONTAINER_LABEL},
        )
        now = time.time()
        running: list[tuple[int, Container]] = []
        for container in containers:
            raw_user_id = container.labels.get(_CONTAINER_LABEL)
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            with self._resource_lock:
                self._user_guards.setdefault(user_id, _LifecycleGuard())
                self._start_locks.setdefault(user_id, threading.Lock())
            with self._activity_lock:
                self._last_activity.setdefault(user_id, now)
            container.reload()
            if container.status == "running":
                running.append((user_id, container))

        keep = running[: self._config.max_running_containers]
        overflow = running[self._config.max_running_containers :]
        with self._capacity_condition:
            self._running_users = {user_id for user_id, _ in keep}
            self._reserved_users.clear()
        for user_id, container in overflow:
            container.stop(timeout=10)
            logger.info(f"Stop excess Docker sandbox during startup: user_id={user_id}")

    @contextmanager
    def _open_archive_sync(
        self,
        container: Container,
        path: str,
    ) -> Iterator[tarfile.TarFile]:
        """流式打开容器中的 archive，适用于运行或停止状态"""
        chunks, _ = container.get_archive(path)
        raw_reader = _IteratorReader(iter(chunks))
        buffered_reader = io.BufferedReader(raw_reader)
        try:
            with tarfile.open(fileobj=buffered_reader, mode="r|*") as archive:
                yield archive
        finally:
            buffered_reader.close()

    def _inspect_archive_path_sync(
        self,
        container: Container,
        path: str,
    ) -> tarfile.TarInfo | None:
        """读取容器路径对应的首个 archive 条目"""
        try:
            with self._open_archive_sync(container, path) as archive:
                member = next(iter(archive), None)
                if member is None:
                    return None
                return member
        except NotFound:
            return None

    def _read_archive_file_sync(
        self,
        container: Container,
        path: str,
        max_bytes: int,
    ) -> tuple[bytes, tarfile.TarInfo]:
        """从运行或停止容器读取一个普通文件"""
        with self._open_archive_sync(container, path) as archive:
            member = next(iter(archive), None)
            if member is None or not member.isreg():
                raise FileNotFoundError(path)
            if member.size > max_bytes:
                raise SandboxFileTooLargeError(
                    f"file too large: {member.size} > {max_bytes}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise FileNotFoundError(path)
            content = extracted.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise SandboxFileTooLargeError(f"file too large: > {max_bytes}")
            return content, member

    def _put_archive_sync(
        self,
        container: Container,
        base_path: str,
        directories: list[tuple[str, int, int]],
        files: list[tuple[str, int, int, BinaryIO, int]],
    ) -> None:
        """构造受控 tar 并写入运行或停止容器"""
        with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as buffer:
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                for name, owner_uid, mode in directories:
                    info = tarfile.TarInfo(name=name.rstrip("/") + "/")
                    info.type = tarfile.DIRTYPE
                    info.mode = mode
                    info.uid = owner_uid
                    info.gid = owner_uid
                    archive.addfile(info)
                for name, owner_uid, mode, content, size in files:
                    info = tarfile.TarInfo(name=name)
                    info.size = size
                    info.mode = mode
                    info.uid = owner_uid
                    info.gid = owner_uid
                    archive.addfile(info, content)
            buffer.seek(0)
            if not container.put_archive(base_path, buffer):
                raise OSError(f"Failed to write Docker archive: {base_path}")

    def _write_uid_registry_sync(
        self,
        container: Container,
        mapping: dict[str, int],
    ) -> None:
        """将会话 UID 注册表持久化到用户数据卷"""
        content = json.dumps(
            {"version": _UID_REGISTRY_VERSION, "conversations": mapping},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._put_archive_sync(
            container,
            "/workspace",
            [],
            [
                (
                    PurePosixPath(_SANDBOX_UID_REGISTRY).name,
                    0,
                    0o600,
                    io.BytesIO(content),
                    len(content),
                )
            ],
        )

    def _scan_workspace_uids_sync(self, container: Container) -> dict[str, int]:
        """首次迁移时从目录属主构建完整会话 UID 注册表"""
        mapping: dict[str, int] = {}
        try:
            with self._open_archive_sync(container, _SANDBOX_WORKSPACE_ROOT) as archive:
                for member in archive:
                    if not member.isdir():
                        continue
                    parts = PurePosixPath(member.name).parts
                    if "conversations" in parts:
                        parts = parts[parts.index("conversations") + 1 :]
                    if len(parts) != 1:
                        continue
                    try:
                        conversation_id = str(UUID(parts[0]))
                    except ValueError:
                        continue
                    if _MIN_CONVERSATION_UID <= member.uid <= _MAX_CONVERSATION_UID:
                        mapping[conversation_id] = member.uid
        except NotFound:
            pass
        return mapping

    def _load_uid_registry_sync(self, container: Container) -> dict[str, int]:
        """读取 UID 注册表，不存在时从已有工作区迁移"""
        try:
            content, member = self._read_archive_file_sync(
                container,
                _SANDBOX_UID_REGISTRY,
                4 * 1024 * 1024,
            )
            if member.uid != 0:
                raise RuntimeError("Sandbox UID registry has an invalid owner")
            payload = json.loads(content)
            if payload.get("version") != _UID_REGISTRY_VERSION:
                raise RuntimeError("Unsupported sandbox UID registry version")
            raw_mapping = payload.get("conversations")
            if not isinstance(raw_mapping, dict):
                raise TypeError("Invalid sandbox UID registry")
            mapping = {str(UUID(key)): int(value) for key, value in raw_mapping.items()}
        except (NotFound, FileNotFoundError):
            mapping = self._scan_workspace_uids_sync(container)
            self._write_uid_registry_sync(container, mapping)
        if len(mapping.values()) != len(set(mapping.values())):
            raise RuntimeError("Sandbox UID registry contains duplicate UIDs")
        if any(
            uid < _MIN_CONVERSATION_UID or uid > _MAX_CONVERSATION_UID
            for uid in mapping.values()
        ):
            raise RuntimeError("Sandbox UID registry contains an invalid UID")
        return mapping

    def _allocate_conversation_uid(
        self,
        conversation_id: UUID,
        used_uids: set[int],
    ) -> int:
        """为会话确定性分配未使用的 Linux UID"""
        uid_range = _MAX_CONVERSATION_UID - _MIN_CONVERSATION_UID + 1
        for attempt in range(uid_range):
            digest = hashlib.blake2s(
                conversation_id.bytes + attempt.to_bytes(8, "big")
            ).digest()
            candidate = (
                _MIN_CONVERSATION_UID + int.from_bytes(digest[:8], "big") % uid_range
            )
            if candidate not in used_uids:
                return candidate
        raise RuntimeError("conversation uid range exhausted")

    def _ensure_workspace_archive_sync(
        self,
        container: Container,
        conversation_id: UUID,
    ) -> int:
        """不启动容器地创建会话工作区并返回稳定 UID"""
        self._put_archive_sync(
            container,
            "/workspace",
            [
                ("conversations", 0, 0o711),
                (PurePosixPath(_SANDBOX_STAGING_ROOT).name, 0, 0o700),
            ],
            [],
        )
        mapping = self._load_uid_registry_sync(container)
        key = str(conversation_id)
        conversation_uid = mapping.get(key)
        if conversation_uid is None:
            conversation_uid = self._allocate_conversation_uid(
                conversation_id,
                set(mapping.values()),
            )
            mapping[key] = conversation_uid
            self._write_uid_registry_sync(container, mapping)

        target_path = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        existing = self._inspect_archive_path_sync(container, target_path)
        if existing is not None and (
            not existing.isdir() or existing.uid != conversation_uid
        ):
            raise RuntimeError(
                "Conversation workspace owner does not match UID registry"
            )

        conversation_name = str(conversation_id)
        self._put_archive_sync(
            container,
            _SANDBOX_WORKSPACE_ROOT,
            [
                (conversation_name, conversation_uid, 0o700),
                (f"{conversation_name}/.home", conversation_uid, 0o700),
                (f"{conversation_name}/.cache", conversation_uid, 0o700),
                (f"{conversation_name}/.cache/uv", conversation_uid, 0o700),
                (f"{conversation_name}/.tmp", conversation_uid, 0o700),
            ],
            [],
        )
        self._put_archive_sync(
            container,
            _SANDBOX_STAGING_ROOT,
            [(conversation_name, 0, 0o700)],
            [],
        )
        return conversation_uid

    async def get_backend(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> DockerSandboxBackend:
        """获取用户指定会话的沙盒后端"""
        await self.init()
        (
            user_lock,
            user_guard,
            start_lock,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            raise RuntimeError("Conversation sandbox guard is unavailable")

        def prepare() -> int:
            with user_guard.maintenance(), conversation_guard.maintenance():
                container = self._get_or_create_storage_container_sync(user_id)
                return self._ensure_workspace_archive_sync(
                    container,
                    conversation_id,
                )

        async with user_lock:
            conversation_uid = await asyncio.to_thread(prepare)
        self._touch_user(user_id)
        backend = DockerSandboxBackend(
            user_id,
            conversation_id,
            conversation_uid,
            self._config,
            user_guard,
            conversation_guard,
            mutation_lock,
            lambda: self._touch_user(user_id),
            lambda: self._get_running_container_sync(
                user_id,
                start_lock,
            ),
        )
        return backend

    def _validate_attachment_target_sync(
        self,
        container: Container,
        conversation_id: UUID,
        conversation_uid: int,
        relative_path: str,
    ) -> tuple[list[tuple[str, int, int]], int]:
        """校验附件路径中的每个组件并返回待创建目录和被替换大小"""
        workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        root_info = self._inspect_archive_path_sync(container, workspace)
        if (
            root_info is None
            or not root_info.isdir()
            or root_info.uid != conversation_uid
        ):
            raise OSError("Invalid conversation workspace")

        parts = PurePosixPath(relative_path).parts
        directories: list[tuple[str, int, int]] = []
        current_path = workspace
        for index, component in enumerate(parts[:-1], start=1):
            current_path = posixpath.join(current_path, component)
            info = self._inspect_archive_path_sync(container, current_path)
            if info is None:
                directories.append(("/".join(parts[:index]), conversation_uid, 0o700))
                continue
            if not info.isdir() or info.uid != conversation_uid:
                raise SandboxPathError(relative_path)

        target_path = posixpath.join(workspace, relative_path)
        target_info = self._inspect_archive_path_sync(container, target_path)
        if target_info is None:
            return directories, 0
        if not target_info.isreg() or target_info.uid != conversation_uid:
            raise SandboxPathError(relative_path)
        return directories, target_info.size

    def _workspace_archive_size_sync(
        self,
        container: Container,
        conversation_id: UUID,
        conversation_uid: int,
    ) -> int:
        """在停止状态下流式统计会话工作区普通文件大小"""
        workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        total = 0
        with self._open_archive_sync(container, workspace) as archive:
            for member in archive:
                if member.isreg():
                    if member.uid != conversation_uid:
                        raise OSError("Conversation workspace contains invalid owner")
                    total += member.size
                    if total > self._config.max_workspace_bytes:
                        break
        return total

    def _upload_attachment_sync(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        content: BinaryIO,
        user_guard: _LifecycleGuard,
        conversation_guard: _LifecycleGuard,
        mutation_lock: threading.RLock,
    ) -> None:
        """使用 Docker Archive API 上传附件，不启动容器"""
        with (
            user_guard.maintenance(),
            conversation_guard.maintenance(),
            mutation_lock,
        ):
            container = self._get_or_create_storage_container_sync(user_id)
            conversation_uid = self._ensure_workspace_archive_sync(
                container,
                conversation_id,
            )
            content.seek(0, io.SEEK_END)
            size = content.tell()
            content.seek(0)
            if size > self._config.max_file_bytes:
                raise SandboxFileTooLargeError(
                    f"file too large: {size} > {self._config.max_file_bytes}"
                )
            directories, replaced_size = self._validate_attachment_target_sync(
                container,
                conversation_id,
                conversation_uid,
                normalized_path,
            )
            current_size = self._workspace_archive_size_sync(
                container,
                conversation_id,
                conversation_uid,
            )
            projected_size = current_size - replaced_size + size
            if projected_size > self._config.max_workspace_bytes:
                raise SandboxStorageLimitError(
                    "workspace limit exceeded: "
                    f"{projected_size} > {self._config.max_workspace_bytes}"
                )
            workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
            self._put_archive_sync(
                container,
                workspace,
                directories,
                [
                    (
                        normalized_path,
                        conversation_uid,
                        0o600,
                        content,
                        size,
                    )
                ],
            )
            written = self._inspect_archive_path_sync(
                container,
                posixpath.join(workspace, normalized_path),
            )
            if (
                written is None
                or not written.isreg()
                or written.uid != conversation_uid
                or written.size != size
            ):
                raise OSError("Uploaded attachment failed validation")

    def _download_attachment_sync(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        user_guard: _LifecycleGuard,
        conversation_guard: _LifecycleGuard,
    ) -> bytes:
        """使用 Docker Archive API 下载附件，不启动容器"""
        with user_guard.maintenance(), conversation_guard.maintenance():
            container = self._get_or_create_storage_container_sync(user_id)
            conversation_uid = self._ensure_workspace_archive_sync(
                container,
                conversation_id,
            )
            self._validate_attachment_target_sync(
                container,
                conversation_id,
                conversation_uid,
                normalized_path,
            )
            workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
            content, member = self._read_archive_file_sync(
                container,
                posixpath.join(workspace, normalized_path),
                self._config.max_file_bytes,
            )
            if member.uid != conversation_uid:
                raise FileNotFoundError(normalized_path)
            return content

    async def upload_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> None:
        """上传文件对象到用户会话目录"""
        normalized_path = normalize_attachment_path(path)
        await self.init()
        (
            user_lock,
            user_guard,
            _,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            raise RuntimeError("Conversation sandbox guard is unavailable")
        async with user_lock:
            await asyncio.to_thread(
                self._upload_attachment_sync,
                user_id,
                conversation_id,
                normalized_path,
                content,
                user_guard,
                conversation_guard,
                mutation_lock,
            )
        self._touch_user(user_id)

    async def download_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bytes:
        """下载用户会话目录中的文件"""
        normalized_path = normalize_attachment_path(path)
        await self.init()
        user_lock, user_guard, _, conversation_guard, _ = await self._get_resources(
            user_id,
            conversation_id,
        )
        if conversation_guard is None:
            raise RuntimeError("Conversation sandbox guard is unavailable")
        async with user_lock:
            try:
                content = await asyncio.to_thread(
                    self._download_attachment_sync,
                    user_id,
                    conversation_id,
                    normalized_path,
                    user_guard,
                    conversation_guard,
                )
            except NotFound:
                raise FileNotFoundError(normalized_path) from None
        self._touch_user(user_id)
        return content

    async def delete_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> None:
        """删除用户会话目录中的文件"""
        normalized_path = normalize_attachment_path(path)
        backend = await self.get_backend(user_id, conversation_id)
        if not await asyncio.to_thread(backend.is_file, normalized_path):
            return
        result = await backend.adelete(normalized_path)
        if result.error and "not found" not in result.error:
            raise OSError(result.error)

    async def is_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bool:
        """检查用户会话目录中的文件是否存在"""
        normalized_path = normalize_attachment_path(path)
        await self.init()
        user_lock, user_guard, _, conversation_guard, _ = await self._get_resources(
            user_id,
            conversation_id,
        )
        if conversation_guard is None:
            return False

        def inspect() -> bool:
            with user_guard.maintenance(), conversation_guard.maintenance():
                container = self._get_or_create_storage_container_sync(user_id)
                conversation_uid = self._ensure_workspace_archive_sync(
                    container,
                    conversation_id,
                )
                try:
                    self._validate_attachment_target_sync(
                        container,
                        conversation_id,
                        conversation_uid,
                        normalized_path,
                    )
                except SandboxPathError:
                    return False
                target = self._inspect_archive_path_sync(
                    container,
                    posixpath.join(
                        _SANDBOX_WORKSPACE_ROOT,
                        str(conversation_id),
                        normalized_path,
                    ),
                )
                return bool(
                    target is not None
                    and target.isreg()
                    and target.uid == conversation_uid
                )

        async with user_lock:
            result = await asyncio.to_thread(inspect)
        self._touch_user(user_id)
        return result

    async def delete_conversation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """删除用户沙盒中的会话目录"""
        await self.init()
        (
            user_lock,
            user_guard,
            start_lock,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            return

        def delete() -> None:
            with (
                user_guard.maintenance(),
                conversation_guard.maintenance(),
                mutation_lock,
            ):
                container = self._get_running_container_sync(user_id, start_lock)
                self._ensure_workspace_archive_sync(container, conversation_id)
                result = container.exec_run(
                    [
                        "rm",
                        "-rf",
                        "--",
                        f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}",
                        posixpath.join(
                            _SANDBOX_STAGING_ROOT,
                            str(conversation_id),
                        ),
                    ],
                    user="0",
                    privileged=True,
                    workdir="/workspace",
                )
                if result.exit_code != 0:
                    raw_output = result.output or b""
                    detail = (
                        raw_output.decode("utf-8", errors="replace")
                        if isinstance(raw_output, bytes)
                        else str(raw_output)
                    ).strip()
                    raise OSError(detail or "failed to delete conversation sandbox")
                mapping = self._load_uid_registry_sync(container)
                mapping.pop(str(conversation_id), None)
                self._write_uid_registry_sync(container, mapping)
                conversation_guard.mark_deleted()

        async with user_lock:
            await asyncio.to_thread(delete)
        self._touch_user(user_id)

    async def delete_user_sandbox(self, user_id: int) -> None:
        """删除用户容器及其持久化数据卷"""
        await self.init()
        user_lock, user_guard, _, _, _ = await self._get_resources(user_id)

        def delete() -> None:
            with user_guard.maintenance():
                client = self._get_client()
                try:
                    client.containers.get(self._container_name(user_id)).remove(
                        force=True
                    )
                except NotFound:
                    pass
                try:
                    client.volumes.get(self._volume_name(user_id)).remove(force=True)
                except NotFound:
                    pass
                user_guard.mark_deleted()

        async with user_lock:
            await asyncio.to_thread(delete)
        self._mark_user_not_running(user_id)
        with self._resource_lock:
            self._user_locks.pop(user_id, None)
            self._user_guards.pop(user_id, None)
            self._start_locks.pop(user_id, None)
            for key in [key for key in self._conversation_guards if key[0] == user_id]:
                self._conversation_guards.pop(key, None)
                self._mutation_locks.pop(key, None)
        with self._activity_lock:
            self._last_activity.pop(user_id, None)

    async def _cleanup_idle_containers(self) -> None:
        """定期停止或删除空闲容器，并始终保留数据卷"""
        while True:
            await asyncio.sleep(self._config.cleanup_interval_seconds)
            with self._activity_lock:
                user_ids = set(self._last_activity)
            user_ids.update(await asyncio.to_thread(self._managed_user_ids_sync))
            for user_id in user_ids:
                if self._idle_seconds(user_id) < self._config.idle_stop_seconds:
                    continue
                user_lock, user_guard, start_lock, _, _ = await self._get_resources(
                    user_id
                )
                async with user_lock:
                    await asyncio.to_thread(
                        self._cleanup_idle_container_sync,
                        user_id,
                        user_guard,
                        start_lock,
                    )

    def _managed_user_ids_sync(self) -> set[int]:
        """列出 Docker 中已有的用户沙盒"""
        user_ids: set[int] = set()
        containers = self._get_client().containers.list(
            all=True,
            filters={"label": _CONTAINER_LABEL},
        )
        for container in containers:
            raw_user_id = container.labels.get(_CONTAINER_LABEL)
            try:
                user_ids.add(int(raw_user_id))
            except (TypeError, ValueError):
                logger.warning(
                    f"Ignore Docker sandbox with invalid user label: container={container.name}"
                )
        return user_ids

    def _cleanup_idle_container_sync(
        self,
        user_id: int,
        user_guard: _LifecycleGuard,
        start_lock: threading.Lock,
    ) -> None:
        """在没有活跃操作时停止或删除空闲用户容器"""
        idle_seconds = self._idle_seconds(user_id)
        if idle_seconds < self._config.idle_stop_seconds:
            return
        with user_guard.try_maintenance() as acquired:
            if not acquired:
                return
            with start_lock:
                idle_seconds = self._idle_seconds(user_id)
                if idle_seconds < self._config.idle_stop_seconds:
                    return
                container = self._get_existing_container_sync(user_id)
                if container is None:
                    return
                if idle_seconds >= self._config.idle_remove_seconds:
                    container.remove(force=True)
                    self._mark_user_not_running(user_id)
                    with self._activity_lock:
                        self._last_activity.pop(user_id, None)
                    logger.info(
                        "Remove idle Docker sandbox and preserve volume: "
                        f"user_id={user_id}"
                    )
                    return
                if container.status == "running":
                    container.stop(timeout=10)
                    self._mark_user_not_running(user_id)
                    logger.info(f"Stop idle Docker sandbox: user_id={user_id}")

    def _stop_all_containers_sync(self) -> None:
        """停止本应用管理的全部运行中容器"""
        containers = self._get_client().containers.list(
            filters={"label": _CONTAINER_LABEL}
        )
        for container in containers:
            try:
                raw_user_id = container.labels.get(_CONTAINER_LABEL)
                container.stop(timeout=10)
                try:
                    self._mark_user_not_running(int(raw_user_id))
                except (TypeError, ValueError):
                    pass
            except NotFound:
                continue

    async def close(self) -> None:
        """停止后台任务并关闭 Docker 客户端"""
        cleanup_task = self._cleanup_task
        self._cleanup_task = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
        client = self._client
        if client is not None:
            try:
                if self._config.stop_containers_on_shutdown:
                    await asyncio.to_thread(self._stop_all_containers_sync)
            finally:
                self._client = None
                await asyncio.to_thread(client.close)


docker_sandbox_manager = DockerSandboxManager(cfg.sandbox)
