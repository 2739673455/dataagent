"""Docker 沙箱资源与工作区管理。"""

import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from contextlib import suppress
from typing import Any, BinaryIO
from uuid import UUID

from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container
from docker.models.volumes import Volume
from loguru import logger

import docker
from app.sandbox.archive import SandboxArchiveStore
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.ownership import SandboxOwnership
from app.sandbox.paths import (
    SANDBOX_DATA_ROOT,
    SandboxReadonlyMount,
    SandboxSessionScope,
    normalize_attachment_path,
    normalize_user_attachment_path,
)
from app.sandbox.runtime_pool import DockerRuntimePool
from app.shared.config.app_config import SandboxConfig

_DEPLOYMENT_LABEL = "dataagent.sandbox.deployment"
_USER_LABEL = "dataagent.sandbox.user_id"
_QUOTA_BYTES_LABEL = "dataagent.sandbox.quota_bytes"
_CONTAINER_SPEC_LABEL = "dataagent.sandbox.spec"


class DockerSandboxManager:
    """管理每个用户唯一的本地 Docker 沙箱。"""

    def __init__(
        self,
        sandbox_config: SandboxConfig,
        ownership: SandboxOwnership,
        readonly_mounts: Sequence[SandboxReadonlyMount],
    ) -> None:
        """初始化 Docker 沙箱管理器。"""
        self._config = sandbox_config
        self._ownership = ownership
        self._readonly_mounts = tuple(
            sorted(readonly_mounts, key=lambda mount: mount.target.as_posix())
        )
        sources = [mount.source for mount in self._readonly_mounts]
        targets = [mount.target for mount in self._readonly_mounts]
        if len(sources) != len(set(sources)):
            raise ValueError("沙箱只读挂载包含重复源目录")
        if len(targets) != len(set(targets)):
            raise ValueError("沙箱只读挂载包含重复目标路径")
        if any(
            left != right and (left.is_relative_to(right) or right.is_relative_to(left))
            for index, left in enumerate(targets)
            for right in targets[index + 1 :]
        ):
            raise ValueError("沙箱只读挂载目标路径不能互相嵌套")
        self._client: docker.DockerClient | None = None
        self._container_spec: str | None = None
        self._init_lock = asyncio.Lock()
        self._archive = SandboxArchiveStore(sandbox_config.max_file_bytes)
        self._runtime_pool = DockerRuntimePool(
            sandbox_config,
            ownership,
            get_or_create_container=self._get_or_create_storage_container_sync,
            get_existing_container=self._get_existing_container_sync,
            running_containers=self._running_containers_sync,
        )
        self._cleanup_consecutive_failures = 0
        self._cleanup_task: asyncio.Task[None] | None = None
        self._ownership_started = False

    def _get_client(self) -> docker.DockerClient:
        """获取已初始化的 Docker 客户端。"""
        if self._client is None:
            raise RuntimeError("Docker 沙箱管理器尚未初始化")
        return self._client

    def _init_sync(self) -> None:
        """连接 Docker 并加载沙箱镜像。"""
        client = docker.from_env()
        try:
            client.ping()
            try:
                image = client.images.get(self._config.image)
            except ImageNotFound as exc:
                raise RuntimeError(
                    f"Docker 沙箱镜像不存在: {self._config.image}，"
                    "请先执行 docker compose -f docker/compose.yml up -d"
                ) from exc
            if image.id is None:
                raise RuntimeError("Docker 沙箱镜像缺少不可变 ID")
            self._container_spec = self._container_spec_digest(image.id)
        except Exception:
            client.close()
            raise
        self._client = client

    async def init(self, *, start_cleanup: bool = True) -> None:
        """初始化 Docker 沙箱管理器。"""
        async with self._init_lock:
            if not self._ownership_started:
                await asyncio.to_thread(self._ownership.start_runtime)
                self._ownership_started = True
            if self._client is None:
                try:
                    await asyncio.to_thread(self._init_sync)
                    await asyncio.to_thread(self._runtime_pool.reconcile)
                except Exception:
                    with self._ownership.release_runtime():
                        pass
                    self._ownership_started = False
                    raise
            if start_cleanup and self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(
                    self._cleanup_idle_containers()
                )

    def _touch_user(self, user_id: int) -> None:
        """记录用户沙箱最近活动时间。"""
        activity_at = time.time()
        self._ownership.touch(user_id, activity_at)

    def _container_name(self, user_id: int) -> str:
        """构造用户容器名称。"""
        return f"dataagent-{self._config.deployment_namespace}-sandbox-user-{user_id}"

    def _volume_name(self, user_id: int) -> str:
        """构造用户数据卷名称。"""
        return f"{self._container_name(user_id)}-data"

    def _resource_labels(self, user_id: int) -> dict[str, str]:
        """构造容器和卷的归属标签。"""
        return {
            _DEPLOYMENT_LABEL: self._config.deployment_namespace,
            _USER_LABEL: str(user_id),
            _QUOTA_BYTES_LABEL: str(self._config.max_user_storage_bytes),
        }

    def _container_filters(self) -> dict[str, str | list[str] | bool]:
        """构造当前部署实例的 Docker 资源过滤条件。"""
        return {
            "label": [
                f"{_DEPLOYMENT_LABEL}={self._config.deployment_namespace}",
                _USER_LABEL,
            ]
        }

    def _volume_driver_options(self, user_id: int) -> dict[str, str]:
        """渲染用户卷驱动参数。"""
        fields = {
            "deployment_namespace": self._config.deployment_namespace,
            "user_id": user_id,
            "max_user_storage_bytes": self._config.max_user_storage_bytes,
        }
        return {
            key: value.format_map(fields)
            for key, value in self._config.volume_driver_options.items()
        }

    def _runtime_container_spec(self) -> dict[str, Any]:
        """返回创建容器使用的完整运行规格。"""
        return {
            "command": ["sleep", "infinity"],
            "init": True,
            "read_only": True,
            "user": "1000:1000",
            "working_dir": SANDBOX_DATA_ROOT,
            "tmpfs": {"/tmp": "rw,nosuid,nodev,size=256m"},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": self._config.memory_limit,
            "nano_cpus": self._config.nano_cpus,
            "pids_limit": self._config.pids_limit,
            "network_mode": self._config.network_mode,
            "environment": {"HOME": "/tmp"},
        }

    def _readonly_mount_volumes(self) -> dict[str, dict[str, str]]:
        """构造宿主机只读目录的 Docker 挂载参数。"""
        return {
            str(mount.source): {
                "bind": mount.target.as_posix(),
                "mode": "ro",
            }
            for mount in self._readonly_mounts
        }

    def _container_spec_digest(self, image_id: str) -> str:
        """计算完整容器运行和存储规格的稳定摘要。"""
        spec_payload = {
            "layout_version": 7,
            "image_id": image_id,
            "runtime": self._runtime_container_spec(),
            "workspace_mount": {
                "target": SANDBOX_DATA_ROOT,
                "mode": "rw",
            },
            "readonly_mounts": [
                {
                    "source": str(mount.source),
                    "target": mount.target.as_posix(),
                    "mode": "ro",
                }
                for mount in self._readonly_mounts
            ],
            "volume": {
                "driver": self._config.volume_driver,
                "driver_options": self._config.volume_driver_options,
                "quota_bytes": self._config.max_user_storage_bytes,
            },
        }
        return hashlib.sha256(
            json.dumps(spec_payload, sort_keys=True).encode()
        ).hexdigest()

    def _get_existing_volume_sync(self, user_id: int) -> Volume | None:
        """获取并校验已存在的用户数据卷。"""
        client = self._get_client()
        volume_name = self._volume_name(user_id)
        try:
            volume = client.volumes.get(volume_name)
        except NotFound:
            return None
        volume.reload()
        expected_labels = self._resource_labels(user_id)
        actual_labels = volume.attrs.get("Labels") or {}
        if any(
            actual_labels.get(key) != value for key, value in expected_labels.items()
        ):
            raise RuntimeError(f"Docker 数据卷名称已被占用: {volume_name}")
        actual_driver = volume.attrs.get("Driver")
        actual_options = volume.attrs.get("Options") or {}
        expected_options = self._volume_driver_options(user_id)
        if (
            actual_driver != self._config.volume_driver
            or actual_options != expected_options
        ):
            raise RuntimeError(
                f"Docker 数据卷存储策略发生变更，需要迁移: {volume_name}"
            )
        return volume

    def _get_or_create_volume(self, user_id: int) -> Volume:
        """获取或创建用户数据卷。"""
        volume = self._get_existing_volume_sync(user_id)
        if volume is not None:
            return volume
        return self._get_client().volumes.create(
            name=self._volume_name(user_id),
            driver=self._config.volume_driver,
            driver_opts=self._volume_driver_options(user_id),
            labels=self._resource_labels(user_id),
        )

    def _create_container(self, user_id: int) -> Container:
        """创建保持停止状态的用户容器。"""
        client = self._get_client()
        volume = self._get_or_create_volume(user_id)
        if self._container_spec is None:
            raise RuntimeError("Docker 沙箱容器配置不可用")

        container = client.containers.create(
            self._config.image,
            name=self._container_name(user_id),
            volumes={
                volume.name: {"bind": SANDBOX_DATA_ROOT, "mode": "rw"},
                **self._readonly_mount_volumes(),
            },
            labels={
                **self._resource_labels(user_id),
                _CONTAINER_SPEC_LABEL: self._container_spec,
            },
            **self._runtime_container_spec(),
        )
        logger.info(f"创建已停止的用户 Docker 沙箱: user_id={user_id}")
        return container

    def _get_or_create_storage_container_sync(self, user_id: int) -> Container:
        """获取或创建容器，但不启动容器。"""
        if user_id < 0:
            raise ValueError("user_id 不能为负数")
        name = self._container_name(user_id)
        container = self._get_existing_container_sync(user_id)
        if container is not None:
            if container.labels.get(_CONTAINER_SPEC_LABEL) == self._container_spec:
                return container
            logger.info(f"重建过期的 Docker 沙箱: user_id={user_id}")
            container.remove(force=True)
        try:
            return self._create_container(user_id)
        except APIError as exc:
            if exc.status_code != 409:
                raise
            existing_container = self._get_existing_container_sync(user_id)
            if existing_container is None:
                raise RuntimeError(f"Docker 容器创建发生并发冲突: {name}") from exc
            return existing_container

    def _get_existing_container_sync(self, user_id: int) -> Container | None:
        """获取已存在的用户容器。"""
        name = self._container_name(user_id)
        try:
            container = self._get_client().containers.get(name)
        except NotFound:
            return None
        container.reload()
        expected_labels = self._resource_labels(user_id)
        if any(
            container.labels.get(key) != value for key, value in expected_labels.items()
        ):
            raise RuntimeError(f"Docker 容器名称已被占用: {name}")
        return container

    def _get_running_storage_container_sync(self, user_id: int) -> Container | None:
        """为已有沙箱数据取得可执行命令的运行中容器。"""
        container = self._get_existing_container_sync(user_id)
        if container is None and self._get_existing_volume_sync(user_id) is None:
            return None
        self._touch_user(user_id)
        return self._runtime_pool.get_running(user_id)

    def _running_containers_sync(self) -> list[tuple[int, Container]]:
        """读取 Docker 中当前部署的运行容器。"""
        running: list[tuple[int, Container]] = []
        containers = self._get_client().containers.list(
            all=True,
            filters=self._container_filters(),
        )
        for container in containers:
            raw_user_id = container.labels.get(_USER_LABEL)
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            container.reload()
            if container.status == "running":
                running.append((user_id, container))
        return running

    def _build_backend(
        self,
        user_id: int,
        conversation_id: UUID,
        conversation_uid: int,
        scope: SandboxSessionScope | None,
        execution_uid: int | None,
    ) -> DockerSandboxBackend:
        """使用已准备的工作区构造沙箱后端。"""
        return DockerSandboxBackend(
            user_id,
            conversation_id,
            conversation_uid,
            self._config,
            self._ownership,
            lambda: self._touch_user(user_id),
            lambda cancel_event: self._runtime_pool.get_running(user_id, cancel_event),
            session_scope=scope,
            execution_uid=execution_uid,
        )

    async def _prepare_backend(
        self,
        user_id: int,
        conversation_id: UUID,
        scope: SandboxSessionScope | None = None,
    ) -> DockerSandboxBackend:
        """准备工作区并创建普通或 Session 后端。"""
        await self.init()

        def prepare() -> tuple[int, int | None]:
            """在独占维护窗口中准备工作区。"""
            with (
                self._ownership.conversation_maintenance(user_id, conversation_id),
                self._ownership.user_mutation(user_id),
            ):
                self._ownership.assert_available(user_id, conversation_id)
                container = self._get_or_create_storage_container_sync(user_id)
                if scope is None:
                    return self._archive.ensure_workspace(
                        container, conversation_id
                    ), None
                return self._archive.ensure_session_workspace(
                    container,
                    conversation_id,
                    scope,
                )

        conversation_uid, execution_uid = await asyncio.to_thread(prepare)
        await asyncio.to_thread(self._touch_user, user_id)
        return self._build_backend(
            user_id,
            conversation_id,
            conversation_uid,
            scope,
            execution_uid,
        )

    async def get_backend(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> DockerSandboxBackend:
        """获取用户指定会话的沙箱后端。"""
        return await self._prepare_backend(user_id, conversation_id)

    async def get_session_backend(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        agent_type: str,
        session_id: str,
    ) -> DockerSandboxBackend:
        """获取独立 Linux 身份的专业 Agent Session 后端。"""
        scope = SandboxSessionScope(analysis_id, agent_type, session_id)
        return await self._prepare_backend(user_id, conversation_id, scope)

    async def delete_session(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        agent_type: str,
        session_id: str,
    ) -> bool:
        """幂等删除专业 Agent Session 的全部沙箱资源。"""
        scope = SandboxSessionScope(analysis_id, agent_type, session_id)
        await self.init()

        def delete() -> bool:
            """在独占维护窗口中删除 Session 沙箱资源。"""
            with self._ownership.conversation_maintenance(user_id, conversation_id):
                self._ownership.assert_available(user_id, conversation_id)
                container = self._get_running_storage_container_sync(user_id)
                if container is None:
                    return False
                with self._ownership.user_mutation(user_id):
                    return self._archive.delete_session(
                        container,
                        conversation_id,
                        scope,
                    )

        deleted = await asyncio.to_thread(delete)
        await asyncio.to_thread(self._touch_user, user_id)
        return deleted

    def _upload_attachment_sync(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        content: BinaryIO,
    ) -> None:
        """使用 Docker Archive API 上传附件，不启动容器。"""
        with (
            self._ownership.conversation_maintenance(user_id, conversation_id),
            self._ownership.user_mutation(user_id),
        ):
            self._ownership.assert_available(user_id, conversation_id)
            container = self._get_or_create_storage_container_sync(user_id)
            self._archive.upload_file(
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
        """从已有沙箱读取附件，不创建容器、卷或工作区。"""
        container = self._get_existing_container_sync(user_id)
        if container is None:
            raise FileNotFoundError(normalized_path)
        return self._archive.download_file(container, conversation_id, normalized_path)

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
        await asyncio.to_thread(self._touch_user, user_id)

    async def write_artifact(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> None:
        """写入可信系统分析产物。"""
        await self._upload_normalized_file(
            user_id,
            conversation_id,
            normalize_attachment_path(path),
            content,
        )

    async def upload_user_attachment(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> str:
        """上传用户可变附件并返回规范化路径。"""
        normalized_path = normalize_user_attachment_path(path)
        await self._upload_normalized_file(
            user_id,
            conversation_id,
            normalized_path,
            content,
        )
        return normalized_path

    async def download_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bytes:
        """下载用户会话目录中的文件。"""
        normalized_path = normalize_attachment_path(path)
        await self.init()
        try:
            content = await asyncio.to_thread(
                self._download_attachment_sync,
                user_id,
                conversation_id,
                normalized_path,
            )
        except NotFound:
            raise FileNotFoundError(normalized_path) from None
        await asyncio.to_thread(self._touch_user, user_id)
        return content

    async def delete_user_attachment(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> None:
        """删除用户可变附件。"""
        normalized_path = normalize_user_attachment_path(path)
        await self.init()

        def delete() -> None:
            """只删除已有文件，避免空删除创建沙箱资源。"""
            with self._ownership.conversation_maintenance(user_id, conversation_id):
                self._ownership.assert_available(user_id, conversation_id)
                container = self._get_running_storage_container_sync(user_id)
                if container is not None:
                    with self._ownership.user_mutation(user_id):
                        self._archive.delete_file(
                            container, conversation_id, normalized_path
                        )

        await asyncio.to_thread(delete)
        await asyncio.to_thread(self._touch_user, user_id)

    async def is_downloadable_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bool:
        """检查用户会话目录中的文件是否可通过附件接口下载。"""
        normalized_path = normalize_attachment_path(path)
        await self.init()

        def inspect() -> bool:
            """检查已有沙箱中是否存在可下载文件。"""
            container = self._get_existing_container_sync(user_id)
            return container is not None and self._archive.is_downloadable_file(
                container, conversation_id, normalized_path
            )

        result = await asyncio.to_thread(inspect)
        await asyncio.to_thread(self._touch_user, user_id)
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
                container = self._get_running_storage_container_sync(user_id)
                if container is not None:
                    with self._ownership.user_mutation(user_id):
                        self._archive.delete_conversation(container, conversation_id)

        await asyncio.to_thread(delete)
        await asyncio.to_thread(self._touch_user, user_id)

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
                client = self._get_client()
                with suppress(NotFound):
                    client.containers.get(self._container_name(user_id)).remove(
                        force=True
                    )
                with suppress(NotFound):
                    client.volumes.get(self._volume_name(user_id)).remove(force=True)

        await asyncio.to_thread(delete)
        await asyncio.to_thread(self._ownership.forget_user, user_id)

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
            user_ids = await asyncio.to_thread(self._managed_user_ids_sync)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(f"资源发现失败: {exc}")
            logger.exception("发现 Docker 沙箱资源失败")
            self._record_cleanup_result(errors)
            return

        for user_id in user_ids:
            try:
                await asyncio.to_thread(self._runtime_pool.cleanup_idle, user_id)
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

    def _managed_user_ids_sync(self) -> set[int]:
        """列出 Docker 中已有的用户沙箱。"""
        user_ids: set[int] = set()
        containers = self._get_client().containers.list(
            all=True,
            filters=self._container_filters(),
        )
        for container in containers:
            raw_user_id = container.labels.get(_USER_LABEL)
            try:
                user_ids.add(int(raw_user_id))
            except (TypeError, ValueError):
                logger.warning(
                    f"忽略包含无效用户标签的 Docker 沙箱: container={container.name}"
                )
        return user_ids

    async def close(self) -> None:
        """停止后台任务并关闭 Docker 客户端。"""
        await self._close(finalize_containers=True)

    async def disconnect(self) -> None:
        """释放短生命周期管理器且保留运行中的沙箱容器。"""
        await self._close(finalize_containers=False)

    async def _close(self, *, finalize_containers: bool) -> None:
        """按调用场景释放 Docker 管理资源。"""
        cleanup_task = self._cleanup_task
        self._cleanup_task = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
        client = self._client

        def release_runtime() -> None:
            """释放运行时租约并按需终止残留容器。"""
            if not self._ownership_started:
                return
            with self._ownership.release_runtime() as last_runtime:
                if finalize_containers and last_runtime and client is not None:
                    containers = client.containers.list(
                        all=True,
                        filters=self._container_filters(),
                    )
                    self._runtime_pool.finalize(containers)

        try:
            await asyncio.to_thread(release_runtime)
        finally:
            self._ownership_started = False
            if client is not None:
                self._client = None
                await asyncio.to_thread(client.close)
            await asyncio.to_thread(self._ownership.close)
