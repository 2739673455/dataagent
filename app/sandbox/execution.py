"""工作区执行身份、容器操作租约与异步取消。"""

from __future__ import annotations

import asyncio
import posixpath
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TypeVar
from uuid import UUID

from docker.models.containers import Container

from app.sandbox.contracts import SANDBOX_STAGING_ROOT, SandboxSessionScope
from app.sandbox.docker_stream import close_exec_stream
from app.sandbox.ownership import RedisSandboxOwnership
from app.sandbox.paths import conversation_workspace_path
from app.sandbox.runtime import DockerRuntime
from app.shared.config.app_config import SandboxConfig

_ResultT = TypeVar("_ResultT")


class SandboxExecution:
    """绑定工作区身份，在文件工具和 Shell 任务间共享执行管理。"""

    def __init__(
        self,
        user_id: int,
        conversation_id: UUID,
        conversation_uid: int,
        sandbox_config: SandboxConfig,
        ownership: RedisSandboxOwnership,
        runtime: DockerRuntime,
        *,
        session_scope: SandboxSessionScope | None = None,
        execution_uid: int | None = None,
    ) -> None:
        """绑定工作区身份、文件权限和共享运行资源。"""
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.conversation_dir = conversation_workspace_path(conversation_id)
        self.session_scope = session_scope
        self.workspace_dir = (
            session_scope.workspace_path(conversation_id)
            if session_scope is not None
            else self.conversation_dir
        )
        self.execution_uid = execution_uid or conversation_uid
        self.execution_gid = conversation_uid
        self.file_mode = 0o640 if session_scope is not None else 0o600
        self.directory_mode = 0o750 if session_scope is not None else 0o700
        self.umask = 0o027 if session_scope is not None else 0o077
        self.internal_command_timeout_seconds = (
            sandbox_config.internal_command_timeout_seconds
        )
        self.staging_dir = posixpath.join(
            SANDBOX_STAGING_ROOT,
            str(conversation_id),
            str(self.execution_uid),
        )
        self.max_file_bytes = sandbox_config.max_file_bytes
        self._ownership = ownership
        self._runtime = runtime
        self._operation_local = threading.local()

    @property
    def id(self) -> str:
        """获取工作区执行身份的唯一标识。"""
        scope = (
            f":{self.session_scope.relative_workspace}"
            if self.session_scope is not None
            else ""
        )
        return f"docker:{self.user_id}:{self.conversation_id}{scope}"

    @property
    def container(self) -> Container:
        """获取当前操作持有的容器实例。"""
        container = getattr(self._operation_local, "container", None)
        if container is None:
            raise RuntimeError("Docker 容器仅在操作期间可用")
        return container

    def sanitize_output(self, message: str | None) -> str | None:
        """隐藏内部暂存目录的实际路径。"""
        if message is None:
            return None
        return message.replace(self.staging_dir, "<sandbox-staging>")

    @contextmanager
    def operation(self) -> Generator[None]:
        """登记 Redis operation lease，并在公开操作结束后记录活动时间。"""
        existing_container = getattr(self._operation_local, "container", None)
        cancel_event = getattr(self._operation_local, "cancel_event", None)
        try:
            with self._ownership.operation(self.user_id, self.conversation_id):
                if existing_container is None:
                    self._operation_local.container = self._runtime.get_running(
                        self.user_id, cancel_event
                    )
                yield
        finally:
            if existing_container is None and hasattr(
                self._operation_local, "container"
            ):
                del self._operation_local.container
            self._runtime.touch(self.user_id)

    async def run_async(
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

    def read_file_bytes(
        self,
        path: str,
        max_bytes: int,
        *,
        from_end: bool = False,
    ) -> tuple[bytes, int | None]:
        """以会话 UID 限长读取文件开头或结尾。"""
        docker_client = self.container.client
        if docker_client is None:
            raise RuntimeError("Docker 容器客户端不可用")
        api_client = docker_client.api
        created = api_client.exec_create(
            self.container.id,
            [
                "timeout",
                "--signal=KILL",
                str(self.internal_command_timeout_seconds),
                "tail" if from_end else "head",
                "-c",
                str(max_bytes),
                "--",
                path,
            ],
            stdout=True,
            stderr=True,
            user=f"{self.execution_uid}:{self.execution_gid}",
            environment={"HOME": f"{self.workspace_dir}/.home"},
            workdir=self.workspace_dir,
        )
        exec_id = created["Id"]
        output = bytearray()
        output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
        try:
            for chunk in output_stream:
                output.extend(chunk)
        finally:
            close_exec_stream(output_stream)
        inspected = api_client.exec_inspect(exec_id)
        return bytes(output), inspected.get("ExitCode")
