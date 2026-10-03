"""沙箱工作区与文件操作的公开入口。"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence
from contextlib import AsyncExitStack
from typing import BinaryIO
from uuid import UUID

from docker.errors import NotFound
from loguru import logger

from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.contracts import (
    SandboxArtifact,
    SandboxReadonlyMount,
    SandboxSessionScope,
)
from app.sandbox.errors import SandboxPathError
from app.sandbox.execution import SandboxExecution
from app.sandbox.ownership import RedisSandboxOwnership
from app.sandbox.paths import resolve_artifact_path, resolve_attachment_path
from app.sandbox.runtime import DockerRuntime
from app.sandbox.storage import SandboxStorage
from app.shared.config.app_config import SandboxConfig


class DockerSandboxManager:
    """提供用户沙箱、会话工作区和文件操作，管理应用内的资源生命周期。"""

    def __init__(
        self,
        sandbox_config: SandboxConfig,
        ownership: RedisSandboxOwnership | None = None,
        readonly_mounts: Sequence[SandboxReadonlyMount] = (),
    ) -> None:
        """初始化 Docker 沙箱管理器。"""
        self._config = sandbox_config
        self._ownership = (
            ownership
            if ownership is not None
            else RedisSandboxOwnership(
                sandbox_config.ownership.redis_url.get_secret_value(),
                sandbox_config.deployment_namespace,
                lock_timeout_seconds=sandbox_config.ownership.lock_timeout_seconds,
                wait_timeout_seconds=sandbox_config.ownership.wait_timeout_seconds,
                lease_seconds=sandbox_config.ownership.lease_seconds,
            )
        )
        self._runtime = DockerRuntime(sandbox_config, self._ownership, readonly_mounts)
        self._init_lock = asyncio.Lock()
        self._storage = SandboxStorage(sandbox_config.max_file_bytes)
        self._cleanup_consecutive_failures = 0
        self._cleanup_task: asyncio.Task[None] | None = None
        self._ownership_started = False

    async def init(self, *, start_cleanup: bool = True) -> None:
        """初始化 Docker 沙箱，失败或取消时释放已取得的资源。"""
        async with self._init_lock:
            if not self._ownership_started or not self._runtime.initialized:
                initialization = asyncio.create_task(
                    asyncio.to_thread(self._initialize_runtime_sync)
                )
                try:
                    # 取消等待不会停止线程；必须等线程结束后再释放它创建的资源。
                    await asyncio.shield(initialization)
                except BaseException:
                    try:
                        await initialization
                    finally:
                        await self.disconnect()
                    raise
            if start_cleanup and self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(
                    self._cleanup_idle_containers()
                )

    async def get_backend(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        scope: SandboxSessionScope | None = None,
    ) -> DockerSandboxBackend:
        """准备会话或 Session 工作区，返回绑定执行身份的后端。"""
        await self.init()

        def prepare() -> tuple[int, int | None]:
            """在独占维护窗口中准备工作区。"""
            with (
                self._ownership.conversation_maintenance(user_id, conversation_id),
                self._ownership.user_mutation(user_id),
            ):
                self._ownership.assert_available(user_id, conversation_id)
                container = self._runtime.get_or_create_storage(user_id)
                if scope is None:
                    return self._storage.ensure_workspace(
                        container, conversation_id
                    ), None
                return self._storage.ensure_session_workspace(
                    container,
                    conversation_id,
                    scope,
                )

        conversation_uid, execution_uid = await asyncio.to_thread(prepare)
        await asyncio.to_thread(self._runtime.touch, user_id)
        return DockerSandboxBackend(
            SandboxExecution(
                user_id,
                conversation_id,
                conversation_uid,
                self._config,
                self._ownership,
                self._runtime,
                session_scope=scope,
                execution_uid=execution_uid,
            )
        )

    async def delete_session(
        self,
        user_id: int,
        conversation_id: UUID,
        scope: SandboxSessionScope,
    ) -> bool:
        """幂等删除专业 Agent Session 的全部沙箱资源。"""
        await self.init()

        def delete() -> bool:
            """在独占维护窗口中删除 Session 沙箱资源。"""
            with self._ownership.conversation_maintenance(user_id, conversation_id):
                self._ownership.assert_available(user_id, conversation_id)
                container = self._runtime.get_existing_running(user_id)
                if container is None:
                    return False
                with self._ownership.user_mutation(user_id):
                    return self._storage.delete_session(
                        container,
                        conversation_id,
                        scope,
                    )

        deleted = await asyncio.to_thread(delete)
        await asyncio.to_thread(self._runtime.touch, user_id)
        return deleted

    async def write_artifact(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
        *,
        session_scope: SandboxSessionScope,
    ) -> str:
        """在指定 Session 内写入产物并返回绝对路径。"""
        artifact = resolve_artifact_path(path, conversation_id, session_scope)
        await self._upload_normalized_file(
            user_id,
            conversation_id,
            artifact.relative_path,
            content,
        )
        return artifact.path

    async def upload_user_attachment(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> str:
        """上传用户可变附件并返回规范化路径。"""
        attachment = resolve_attachment_path(path, conversation_id, writable=True)
        await self._upload_normalized_file(
            user_id,
            conversation_id,
            attachment.relative_path,
            content,
        )
        return attachment.relative_path

    async def download_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bytes:
        """下载用户会话目录中的文件。"""
        attachment = resolve_attachment_path(path, conversation_id)
        await self.init()
        try:
            content = await asyncio.to_thread(
                self._download_attachment_sync,
                user_id,
                conversation_id,
                attachment.relative_path,
            )
        except NotFound:
            raise FileNotFoundError(attachment.relative_path) from None
        await asyncio.to_thread(self._runtime.touch, user_id)
        return content

    async def delete_user_attachment(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> None:
        """删除用户可变附件。"""
        attachment = resolve_attachment_path(path, conversation_id, writable=True)
        await self.init()

        def delete() -> None:
            """只删除已有文件，避免空删除创建沙箱资源。"""
            with self._ownership.conversation_maintenance(user_id, conversation_id):
                self._ownership.assert_available(user_id, conversation_id)
                container = self._runtime.get_existing_running(user_id)
                if container is not None:
                    with self._ownership.user_mutation(user_id):
                        self._storage.delete_file(
                            container, conversation_id, attachment.relative_path
                        )

        await asyncio.to_thread(delete)
        await asyncio.to_thread(self._runtime.touch, user_id)

    async def resolve_artifacts(
        self,
        user_id: int,
        conversation_id: UUID,
        paths: Collection[str],
        *,
        session_scope: SandboxSessionScope | None = None,
    ) -> dict[str, SandboxArtifact]:
        """批量解析可下载产物，以输入引用为键；无效文件省略，设施错误抛出。"""
        candidates: dict[str, SandboxArtifact] = {}
        for path in dict.fromkeys(paths):
            try:
                candidates[path] = resolve_artifact_path(
                    path, conversation_id, session_scope
                )
            except SandboxPathError:
                continue
        if not candidates:
            return {}
        await self.init()

        def inspect() -> dict[str, SandboxArtifact]:
            """在已有容器中按去重路径检查文件，只保留可下载的产物引用。"""
            container = self._runtime.get_existing(user_id)
            if container is None:
                return {}
            downloadable = {
                path: self._storage.is_downloadable_file(
                    container, conversation_id, path
                )
                for path in {artifact.relative_path for artifact in candidates.values()}
            }
            return {
                reference: artifact
                for reference, artifact in candidates.items()
                if downloadable[artifact.relative_path]
            }

        result = await asyncio.to_thread(inspect)
        await asyncio.to_thread(self._runtime.touch, user_id)
        return result

    async def delete_conversation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """删除用户沙箱中的会话目录。"""
        await self.init()

        def delete() -> None:
            """删除会话工作区并更新 UID 注册表。"""
            with self._ownership.conversation_maintenance(
                user_id,
                conversation_id,
            ):
                self._ownership.mark_conversation_deleted(
                    user_id,
                    conversation_id,
                )
                container = self._runtime.get_existing_running(user_id)
                if container is not None:
                    with self._ownership.user_mutation(user_id):
                        self._storage.delete_conversation(container, conversation_id)

        await asyncio.to_thread(delete)
        await asyncio.to_thread(self._runtime.touch, user_id)

    async def delete_user_sandbox(self, user_id: int) -> None:
        """删除用户容器及其持久化数据卷。"""
        await self.init()

        def delete() -> None:
            """删除用户容器和持久化数据卷。"""
            with (
                self._ownership.user_maintenance(user_id),
                self._ownership.capacity(),
                self._ownership.user_mutation(user_id),
            ):
                self._ownership.mark_user_deleted(user_id)
                self._runtime.delete_user_storage(user_id)

        await asyncio.to_thread(delete)
        await asyncio.to_thread(self._ownership.forget_user, user_id)

    async def close(self) -> None:
        """停止后台任务并关闭 Docker 客户端。"""
        await self._close(finalize_containers=True)

    async def disconnect(self) -> None:
        """释放短生命周期管理器且保留运行中的沙箱容器。"""
        await self._close(finalize_containers=False)

    def _initialize_runtime_sync(self) -> None:
        """在同一工作线程完成运行时登记和 Docker 初始化。"""
        if not self._ownership_started:
            self._ownership.start_runtime()
            self._ownership_started = True
        if not self._runtime.initialized:
            self._runtime.initialize()

    def _upload_attachment_sync(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        content: BinaryIO,
    ) -> None:
        """通过 Docker Archive API 向容器上传附件，保留容器运行状态。"""
        with (
            self._ownership.conversation_maintenance(user_id, conversation_id),
            self._ownership.user_mutation(user_id),
        ):
            self._ownership.assert_available(user_id, conversation_id)
            container = self._runtime.get_or_create_storage(user_id)
            self._storage.upload_file(
                container,
                conversation_id,
                normalized_path,
                content,
            )

    def _download_attachment_sync(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
    ) -> bytes:
        """从已有容器的工作区读取附件。"""
        container = self._runtime.get_existing(user_id)
        if container is None:
            raise FileNotFoundError(normalized_path)
        return self._storage.download_file(container, conversation_id, normalized_path)

    async def _upload_normalized_file(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        content: BinaryIO,
    ) -> None:
        """将已校验路径的文件对象写入用户会话目录。"""
        await self.init()
        await asyncio.to_thread(
            self._upload_attachment_sync,
            user_id,
            conversation_id,
            normalized_path,
            content,
        )
        await asyncio.to_thread(self._runtime.touch, user_id)

    def _record_cleanup_result(self, errors: list[str]) -> None:
        """更新连续失败计数，并在达到阈值时记录告警。"""
        if errors:
            self._cleanup_consecutive_failures += 1
            failures = self._cleanup_consecutive_failures
        else:
            self._cleanup_consecutive_failures = 0
            failures = 0
        if failures >= self._config.cleanup_failure_alert_threshold:
            logger.error(
                f"Docker 沙箱清理连续失败: consecutive_failures={failures}, last_error={errors[-1]}"
            )

    async def _run_cleanup_cycle(self) -> None:
        """执行一个带用户级错误隔离的清理周期。"""
        errors: list[str] = []
        try:
            user_ids = await asyncio.to_thread(self._runtime.managed_user_ids)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(f"资源发现失败: {exc}")
            logger.exception("发现 Docker 沙箱资源失败")
            self._record_cleanup_result(errors)
            return

        for user_id in user_ids:
            try:
                await asyncio.to_thread(self._runtime.cleanup_idle, user_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"user_id={user_id}: {exc}")
                logger.exception(f"清理 Docker 沙箱失败: user_id={user_id}")
        self._record_cleanup_result(errors)

    async def _cleanup_idle_containers(self) -> None:
        """定期停止或删除空闲容器，并始终保留数据卷。"""
        while True:
            try:
                await asyncio.sleep(self._config.cleanup_interval_seconds)
                await self._run_cleanup_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                error = f"清理循环失败: {exc}"
                logger.exception("Docker 沙箱清理循环异常")
                self._record_cleanup_result([error])

    async def _close(self, *, finalize_containers: bool) -> None:
        """按调用场景释放 Docker 管理资源。"""
        cleanup_task = self._cleanup_task
        self._cleanup_task = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)

        def release_runtime() -> None:
            """释放运行时租约并按需终止残留容器。"""
            if not self._ownership_started:
                return
            with self._ownership.release_runtime() as last_runtime:
                if finalize_containers and last_runtime and self._runtime.initialized:
                    self._runtime.finalize()

        async with AsyncExitStack() as stack:
            stack.push_async_callback(asyncio.to_thread, self._ownership.close)
            stack.push_async_callback(asyncio.to_thread, self._runtime.close)
            try:
                await asyncio.to_thread(release_runtime)
            finally:
                self._ownership_started = False
