"""Docker 客户端、容器、数据卷和运行容量管理。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from contextlib import suppress
from threading import Event
from typing import Any

from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container
from docker.models.volumes import Volume
from loguru import logger

import docker
from app.sandbox.contracts import SANDBOX_DATA_ROOT, SandboxReadonlyMount
from app.sandbox.errors import SandboxCapacityUnavailableError
from app.sandbox.ownership import RedisSandboxOwnership
from app.shared.config.app_config import SandboxConfig

_DEPLOYMENT_LABEL = "dataagent.sandbox.deployment"
_USER_LABEL = "dataagent.sandbox.user_id"
_QUOTA_BYTES_LABEL = "dataagent.sandbox.quota_bytes"
_CONTAINER_SPEC_LABEL = "dataagent.sandbox.spec"


class DockerRuntime:
    """管理用户容器与持久化卷，并协调启动容量和空闲回收。"""

    def __init__(
        self,
        config: SandboxConfig,
        ownership: RedisSandboxOwnership,
        readonly_mounts: Sequence[SandboxReadonlyMount] = (),
    ) -> None:
        """绑定运行配置、只读挂载和跨进程协调器。"""
        self._config = config
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

    @property
    def initialized(self) -> bool:
        """返回 Docker 客户端是否已经连接。"""
        return self._client is not None

    def close(self) -> None:
        """释放 Docker 客户端。"""
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def delete_user_storage(self, user_id: int) -> None:
        """删除用户容器及其持久化卷，调用方持有维护和容量锁。"""
        client = self._get_client()
        with suppress(NotFound):
            client.containers.get(self._container_name(user_id)).remove(force=True)
        with suppress(NotFound):
            client.volumes.get(self._volume_name(user_id)).remove(force=True)

    def _get_client(self) -> docker.DockerClient:
        """获取已初始化的 Docker 客户端。"""
        if self._client is None:
            raise RuntimeError("Docker 沙箱管理器尚未初始化")
        return self._client

    def initialize(self) -> None:
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
        self.reconcile()

    def touch(self, user_id: int) -> None:
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

    def get_or_create_storage(self, user_id: int) -> Container:
        """获取已有容器或创建处于停止状态的容器。"""
        if user_id < 0:
            raise ValueError("user_id 不能为负数")
        name = self._container_name(user_id)
        container = self.get_existing(user_id)
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
            existing_container = self.get_existing(user_id)
            if existing_container is None:
                raise RuntimeError(f"Docker 容器创建发生并发冲突: {name}") from exc
            return existing_container

    def get_existing(self, user_id: int) -> Container | None:
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

    def get_existing_running(self, user_id: int) -> Container | None:
        """为已有沙箱数据取得可执行命令的运行中容器。"""
        container = self.get_existing(user_id)
        if container is None and self._get_existing_volume_sync(user_id) is None:
            return None
        self.touch(user_id)
        return self.get_running(user_id)

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

    def managed_user_ids(self) -> set[int]:
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

    def get_running(
        self,
        user_id: int,
        cancel_event: Event | None = None,
    ) -> Container:
        """启动用户 Container；满载时回收一个没有操作租约的闲置实例。"""
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        with self._ownership.capacity():
            self._ownership.assert_available(user_id)
            with self._ownership.user_mutation(user_id):
                container = self.get_or_create_storage(user_id)
            if container.status == "running":
                return container

            running = self._running_containers_sync()
            if len(running) < self._config.max_running_containers:
                container.start()
                container.reload()
                logger.info(f"启动 Docker 沙箱: user_id={user_id}")
                return container
            candidates = [
                idle_user_id
                for idle_user_id, _ in sorted(
                    running,
                    key=lambda item: self._ownership.last_activity(item[0]),
                )
                if idle_user_id != user_id
            ]

        for idle_user_id in candidates:
            if self._ownership.is_user_active(idle_user_id):
                continue
            # 所有调用路径都先取得用户维护租约、再取得容量锁。若持有容量锁
            # 等待 operation lease，用户删除会等待该 lease 后再申请容量锁，形成死锁。
            with (
                self._ownership.user_maintenance(idle_user_id),
                self._ownership.capacity(),
            ):
                current = self.get_existing(idle_user_id)
                if (
                    current is None
                    or current.status != "running"
                    or self._ownership.is_user_active(idle_user_id)
                ):
                    continue
                running = self._running_containers_sync()
                if len(running) < self._config.max_running_containers:
                    break
                current.stop(timeout=10)
                logger.info(f"因容量限制停止空闲 Docker 沙箱: user_id={idle_user_id}")
                break
        else:
            raise SandboxCapacityUnavailableError("Docker 沙箱运行容量已满")

        with self._ownership.capacity():
            self._ownership.assert_available(user_id)
            with self._ownership.user_mutation(user_id):
                container = self.get_or_create_storage(user_id)
            if container.status != "running":
                if (
                    len(self._running_containers_sync())
                    >= self._config.max_running_containers
                ):
                    raise SandboxCapacityUnavailableError("Docker 沙箱运行容量已满")
                container.start()
                container.reload()
                logger.info(f"启动 Docker 沙箱: user_id={user_id}")
            return container

    def reconcile(self) -> None:
        """为已运行 Container 初始化活动记录并收敛既有容量。"""
        running = self._running_containers_sync()
        for user_id, _ in running:
            if self._ownership.last_activity(user_id) <= 0:
                self._ownership.touch(user_id, time.time())
        running.sort(
            key=lambda item: self._ownership.last_activity(item[0]), reverse=True
        )
        for user_id, _ in running[self._config.max_running_containers :]:
            with self._ownership.user_maintenance(user_id), self._ownership.capacity():
                current = self.get_existing(user_id)
                if current is not None and current.status == "running":
                    current.stop(timeout=10)
                    logger.info(f"启动时停止超出上限的 Docker 沙箱: user_id={user_id}")

    def cleanup_idle(self, user_id: int) -> None:
        """在用户操作结束后回收闲置 Container，始终保留 Volume。"""
        with self._ownership.user_maintenance(user_id), self._ownership.capacity():
            container = self.get_existing(user_id)
            if container is None:
                return
            idle_seconds = max(
                0.0, time.time() - self._ownership.last_activity(user_id)
            )
            if idle_seconds < self._config.idle_stop_seconds:
                return
            if idle_seconds >= self._config.idle_remove_seconds:
                container.remove(force=True)
                logger.info(
                    f"删除空闲 Docker 沙箱并保留持久化数据卷: user_id={user_id}"
                )
                return
            if container.status == "running":
                container.stop(timeout=10)
                logger.info(f"停止空闲 Docker 沙箱: user_id={user_id}")

    def finalize(self) -> None:
        """在最后一个应用运行时按配置停止 Container。"""
        containers = self._get_client().containers.list(
            all=True, filters=self._container_filters()
        )
        for container in containers:
            raw_user_id = container.labels.get("dataagent.sandbox.user_id")
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            with (
                suppress(NotFound),
                self._ownership.user_maintenance(user_id),
                self._ownership.capacity(),
            ):
                current = self.get_existing(user_id)
                if (
                    current is not None
                    and self._config.stop_containers_on_shutdown
                    and current.status == "running"
                ):
                    current.stop(timeout=10)
