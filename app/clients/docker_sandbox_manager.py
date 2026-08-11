"""本地 Docker 沙盒管理"""

import asyncio
import base64
import io
import json
import os
import posixpath
import secrets
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    DeleteResult,
    EditResult,
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
from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container
from loguru import logger

import docker
from app.conf.app_config import ROOT_DIR, SandboxConfig, cfg

_CONTAINER_LABEL = "insight.sandbox.user_id"
_CONTAINER_PREFIX = "insight-sandbox-user"
_VOLUME_PREFIX = "insight-sandbox-user"
_SANDBOX_WORKSPACE_ROOT = "/workspace/conversations"
_DEFAULT_EXECUTE_TIMEOUT = 120
_EDIT_INLINE_MAX_BYTES = 50_000

_LARGE_EDIT_SCRIPT = """
import base64
import json
import os
import sys

payload = json.loads(base64.b64decode(sys.argv[1]).decode())
try:
    with open(payload["target"], encoding="utf-8") as target_file:
        content = target_file.read()
    with open(payload["old"], encoding="utf-8") as old_file:
        old = old_file.read()
    with open(payload["new"], encoding="utf-8") as new_file:
        new = new_file.read()
    count = content.count(old)
    if count == 0:
        print(json.dumps({"error": "string_not_found"}))
    elif count > 1 and not payload["replace_all"]:
        print(json.dumps({"error": "multiple_occurrences", "count": count}))
    else:
        updated = content.replace(old, new) if payload["replace_all"] else content.replace(old, new, 1)
        with open(payload["target"], "w", encoding="utf-8") as target_file:
            target_file.write(updated)
        print(json.dumps({"count": count}))
finally:
    for path in (payload["old"], payload["new"]):
        try:
            os.remove(path)
        except OSError:
            pass
""".strip()


class SandboxPathError(ValueError):
    """沙盒路径非法"""


def normalize_attachment_path(path: str) -> str:
    """校验并规范化会话内的附件相对路径"""
    if not path or path.startswith(("/", "~")) or "\x00" in path:
        raise SandboxPathError(path)
    parts = PurePosixPath(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SandboxPathError(path)
    return PurePosixPath(*parts).as_posix()


class DockerSandboxBackend(BaseSandbox):
    """将一个用户容器中的会话目录暴露为虚拟文件系统"""

    def __init__(
        self,
        container: Container,
        conversation_id: UUID,
        max_output_bytes: int,
    ) -> None:
        """初始化会话级 Docker 沙盒后端"""
        self._container = container
        self._workspace_dir = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        self._max_output_bytes = max_output_bytes

    @property
    def id(self) -> str:
        """获取沙盒后端唯一标识"""
        return f"{self._container.id}:{PurePosixPath(self._workspace_dir).name}"

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

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """在用户容器的当前会话目录中执行命令"""
        effective_timeout = _DEFAULT_EXECUTE_TIMEOUT if timeout is None else timeout
        shell_command = ["/bin/sh", "-lc", command]
        if effective_timeout > 0:
            shell_command = [
                "timeout",
                "--signal=KILL",
                str(effective_timeout),
                *shell_command,
            ]

        result = self._container.exec_run(
            shell_command,
            workdir=self._workspace_dir,
            demux=False,
        )
        raw_output = result.output or b""
        if isinstance(raw_output, str):
            output_bytes = raw_output.encode()
        elif isinstance(raw_output, bytes):
            output_bytes = raw_output
        else:
            output_bytes = b"".join(raw_output)
        truncated = len(output_bytes) > self._max_output_bytes
        if truncated:
            output_bytes = output_bytes[-self._max_output_bytes :]
        return ExecuteResponse(
            output=output_bytes.decode("utf-8", errors="replace"),
            exit_code=result.exit_code,
            truncated=truncated,
        )

    def ls(self, path: str) -> LsResult:
        """列出当前会话目录内容"""
        result = super().ls(self._resolve_path(path))
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
        result = super().read(self._resolve_path(file_path), offset, limit)
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
        result = super().write(self._resolve_path(file_path), content)
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
        resolved_path = self._resolve_path(file_path)
        payload_size = len(old_string.encode()) + len(new_string.encode())
        if payload_size > _EDIT_INLINE_MAX_BYTES:
            result = self._edit_large_file(
                resolved_path,
                old_string,
                new_string,
                replace_all,
            )
        else:
            result = super().edit(
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

    def _edit_large_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
    ) -> EditResult:
        """通过会话目录内的临时文件编辑大文本"""
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
            return EditResult(error=f"Error editing file '{file_path}': {error}")

        payload = base64.b64encode(
            json.dumps(
                {
                    "target": file_path,
                    "old": old_path,
                    "new": new_path,
                    "replace_all": replace_all,
                }
            ).encode()
        ).decode()
        result = self.execute(
            f"python3 -c {shlex.quote(_LARGE_EDIT_SCRIPT)} {shlex.quote(payload)}"
        )
        try:
            response = json.loads(result.output)
        except json.JSONDecodeError:
            self.execute(f"rm -f {shlex.quote(old_path)} {shlex.quote(new_path)}")
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
        result = super().delete(self._resolve_path(file_path))
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
        result = super().grep(
            pattern,
            self._resolve_path(path or "/"),
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
        result = super().glob(pattern, self._resolve_path(path or "/"))
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
        """通过 Docker Archive API 写入单个文件"""
        parent = posixpath.dirname(path)
        name = posixpath.basename(path)
        self._container.exec_run(["mkdir", "-p", parent])
        with io.BytesIO() as archive_buffer:
            with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
                info = tarfile.TarInfo(name=name)
                info.size = size
                info.mode = 0o600
                info.uid = 1000
                info.gid = 1000
                info.uname = "sandbox"
                info.gname = "sandbox"
                archive.addfile(info, content)
            archive_buffer.seek(0)
            if not self._container.put_archive(parent, archive_buffer):
                raise OSError(f"Failed to upload file: {path}")

    def upload_fileobj(self, path: str, content: BinaryIO) -> FileUploadResponse:
        """上传文件对象到当前会话"""
        try:
            resolved_path = self._resolve_path(path)
            content.seek(0, io.SEEK_END)
            size = content.tell()
            content.seek(0)
            self._put_archive(resolved_path, content, size)
        except SandboxPathError:
            return FileUploadResponse(path=path, error=INVALID_PATH)
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
        for path in paths:
            try:
                resolved_path = self._resolve_path(path)
                chunks, _ = self._container.get_archive(resolved_path)
                archive_bytes = b"".join(chunks)
                with tarfile.open(
                    fileobj=io.BytesIO(archive_bytes), mode="r:"
                ) as archive:
                    member = next(
                        (item for item in archive.getmembers() if item.isfile()),
                        None,
                    )
                    if member is None:
                        responses.append(
                            FileDownloadResponse(path=path, error=IS_DIRECTORY)
                        )
                        continue
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        responses.append(
                            FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
                        )
                        continue
                    responses.append(
                        FileDownloadResponse(path=path, content=extracted.read())
                    )
            except SandboxPathError:
                responses.append(FileDownloadResponse(path=path, error=INVALID_PATH))
            except NotFound:
                responses.append(FileDownloadResponse(path=path, error=FILE_NOT_FOUND))
            except (APIError, OSError, tarfile.TarError) as exc:
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
        result = self._container.exec_run(["test", "-f", resolved_path])
        return result.exit_code == 0


class DockerSandboxManager:
    """管理每个用户唯一的本地 Docker 沙盒"""

    def __init__(self, sandbox_config: SandboxConfig) -> None:
        """初始化 Docker 沙盒管理器"""
        self._config = sandbox_config
        self._client: docker.DockerClient | None = None
        self._container_lock = asyncio.Lock()

    def _get_client(self) -> docker.DockerClient:
        """获取已初始化的 Docker 客户端"""
        if self._client is None:
            raise RuntimeError("Docker sandbox manager is not initialized")
        return self._client

    def _init_sync(self) -> None:
        """连接 Docker 并确保沙盒镜像存在"""
        client = docker.from_env()
        client.ping()
        try:
            client.images.get(self._config.image)
        except ImageNotFound:
            build_context = Path(self._config.build_context)
            if not build_context.is_absolute():
                build_context = ROOT_DIR / build_context
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
            client.images.build(
                path=str(build_context),
                tag=self._config.image,
                rm=True,
                network_mode=self._config.build_network_mode,
                buildargs=proxy_build_args,
            )
        self._client = client

    async def init(self) -> None:
        """初始化 Docker 沙盒管理器"""
        if self._client is None:
            await asyncio.to_thread(self._init_sync)

    def _container_name(self, user_id: int) -> str:
        """构造用户容器名称"""
        return f"{_CONTAINER_PREFIX}-{user_id}"

    def _volume_name(self, user_id: int) -> str:
        """构造用户数据卷名称"""
        return f"{_VOLUME_PREFIX}-{user_id}-data"

    def _create_container(self, user_id: int) -> Container:
        """创建用户容器"""
        client = self._get_client()
        volume_name = self._volume_name(user_id)
        try:
            client.volumes.get(volume_name)
        except NotFound:
            client.volumes.create(
                name=volume_name,
                labels={_CONTAINER_LABEL: str(user_id)},
            )

        container = client.containers.run(
            self._config.image,
            name=self._container_name(user_id),
            command=["sleep", "infinity"],
            detach=True,
            init=True,
            read_only=True,
            user="sandbox",
            working_dir="/workspace",
            volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
            tmpfs={"/tmp": "rw,nosuid,nodev,size=256m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=self._config.memory_limit,
            nano_cpus=self._config.nano_cpus,
            pids_limit=self._config.pids_limit,
            network_mode=self._config.network_mode,
            environment={
                "HOME": "/workspace/.home",
                "UV_CACHE_DIR": "/workspace/.cache/uv",
                "XDG_CACHE_HOME": "/workspace/.cache",
            },
            labels={_CONTAINER_LABEL: str(user_id)},
        )
        logger.info(f"Create user Docker sandbox: user_id={user_id}")
        return container

    def _get_or_create_container_sync(self, user_id: int) -> Container:
        """获取或创建用户容器"""
        if user_id < 0:
            raise ValueError("user_id must be non-negative")
        name = self._container_name(user_id)
        container = self._get_existing_container_sync(user_id)
        if container is not None:
            return container
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
        if container.status != "running":
            container.start()
            container.reload()
        return container

    async def get_backend(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> DockerSandboxBackend:
        """获取用户指定会话的沙盒后端"""
        await self.init()
        async with self._container_lock:
            container = await asyncio.to_thread(
                self._get_or_create_container_sync,
                user_id,
            )
        backend = DockerSandboxBackend(
            container,
            conversation_id,
            self._config.max_output_bytes,
        )
        result = await asyncio.to_thread(
            container.exec_run,
            [
                "mkdir",
                "-p",
                f"conversations/{conversation_id}",
                ".home",
                ".cache/uv",
            ],
            workdir="/workspace",
        )
        if result.exit_code != 0:
            raw_output = result.output
            if isinstance(raw_output, bytes):
                output_bytes = raw_output
            else:
                output_bytes = b"".join(raw_output)
            output = output_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Failed to initialize sandbox workspace: {output.strip()}"
            )
        return backend

    async def upload_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> None:
        """上传文件对象到用户会话目录"""
        normalized_path = normalize_attachment_path(path)
        backend = await self.get_backend(user_id, conversation_id)
        response = await asyncio.to_thread(
            backend.upload_fileobj,
            normalized_path,
            content,
        )
        if response.error:
            raise OSError(response.error)

    async def download_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bytes:
        """下载用户会话目录中的文件"""
        normalized_path = normalize_attachment_path(path)
        backend = await self.get_backend(user_id, conversation_id)
        response = (await backend.adownload_files([normalized_path]))[0]
        if response.error == FILE_NOT_FOUND:
            raise FileNotFoundError(normalized_path)
        if response.error:
            raise OSError(response.error)
        if response.content is None:
            raise FileNotFoundError(normalized_path)
        return response.content

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
        backend = await self.get_backend(user_id, conversation_id)
        return await asyncio.to_thread(backend.is_file, normalized_path)

    async def delete_conversation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """删除用户沙盒中的会话目录"""
        await self.init()
        container = await asyncio.to_thread(
            self._get_existing_container_sync,
            user_id,
        )
        if container is None:
            return
        backend = DockerSandboxBackend(
            container,
            conversation_id,
            self._config.max_output_bytes,
        )
        result = await backend.adelete("/")
        if result.error and "not found" not in result.error:
            raise OSError(result.error)

    async def delete_user_sandbox(self, user_id: int) -> None:
        """删除用户容器及其持久化数据卷"""
        await self.init()

        def delete() -> None:
            client = self._get_client()
            try:
                client.containers.get(self._container_name(user_id)).remove(force=True)
            except NotFound:
                pass
            try:
                client.volumes.get(self._volume_name(user_id)).remove(force=True)
            except NotFound:
                pass

        await asyncio.to_thread(delete)

    async def close(self) -> None:
        """关闭 Docker 客户端并保留用户容器和数据卷"""
        client = self._client
        self._client = None
        if client is not None:
            await asyncio.to_thread(client.close)


docker_sandbox_manager = DockerSandboxManager(cfg.sandbox)
