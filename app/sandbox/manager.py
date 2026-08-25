"""Docker 沙箱资源与工作区管理"""

import asyncio
import hashlib
import io
import json
import posixpath
import tarfile
import tempfile
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from uuid import UUID

from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container
from loguru import logger

import docker
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.capacity import (
    FairCapacityLimiter,
    SandboxCapacitySnapshot,
)
from app.sandbox.concurrency import LifecycleGuard
from app.sandbox.exceptions import (
    SandboxCapacityCancelledError,
    SandboxCapacityTimeoutError,
    SandboxFileTooLargeError,
    SandboxPathError,
    SandboxStorageLimitError,
)
from app.sandbox.ownership import SandboxOwnership
from app.sandbox.paths import (
    SANDBOX_STAGING_ROOT,
    SANDBOX_WORKSPACE_ROOT,
    SandboxSessionScope,
    normalize_attachment_path,
    normalize_user_attachment_path,
)
from app.shared.config.app_config import SandboxConfig

_DEPLOYMENT_LABEL = "dataagent.sandbox.deployment"
_USER_LABEL = "dataagent.sandbox.user_id"
_QUOTA_MODE_LABEL = "dataagent.sandbox.quota_mode"
_QUOTA_BYTES_LABEL = "dataagent.sandbox.quota_bytes"
_MIN_CONVERSATION_UID = 100_000
_MAX_CONVERSATION_UID = 2_147_483_646
_CONTAINER_SPEC_LABEL = "dataagent.sandbox.spec"
_SANDBOX_STAGING_ROOT = SANDBOX_STAGING_ROOT
_SANDBOX_WORKSPACE_ROOT = SANDBOX_WORKSPACE_ROOT
_SANDBOX_UID_REGISTRY = "/workspace/.dataagent-uids.json"
_SANDBOX_ACTIVITY_FILE = "/workspace/.dataagent-activity.json"
_UID_REGISTRY_VERSION = 2
_ACTIVITY_FILE_VERSION = 1
_ARCHIVE_SPOOL_BYTES = 8 * 1024 * 1024


@dataclass(slots=True)
class SandboxUidRegistry:
    """持久化 conversation 和 Agent Session 的 Linux UID"""

    conversations: dict[str, int]
    sessions: dict[str, int]


@dataclass(frozen=True, slots=True)
class DockerSandboxHealth:
    """Docker 沙箱管理器健康状态"""

    cleanup_task_running: bool
    last_cleanup_started_at: float | None
    last_cleanup_completed_at: float | None
    cleanup_consecutive_failures: int
    cleanup_last_error: str | None
    quota_mode: str
    capacity: SandboxCapacitySnapshot


class IteratorReader(io.RawIOBase):
    """将 Docker archive 字节迭代器适配为 tarfile 可读取的流"""

    def __init__(self, chunks):
        """绑定 Docker archive 返回的字节块迭代器"""
        super().__init__()
        self._chunks = chunks
        self._buffer = bytearray()
        self._finished = False

    def readable(self) -> bool:
        """声明该适配器支持读取"""
        return True

    def readinto(self, target: Any) -> int:
        """将迭代器数据填充到目标缓冲区"""
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
        """关闭底层字节迭代器和读取流"""
        close_chunks = getattr(self._chunks, "close", None)
        if callable(close_chunks):
            close_chunks()
        super().close()


class DockerSandboxManager:
    """管理每个用户唯一的本地 Docker 沙盒"""

    def __init__(
        self,
        sandbox_config: SandboxConfig,
        ownership: SandboxOwnership,
    ) -> None:
        """初始化 Docker 沙盒管理器"""
        self._config = sandbox_config
        self._ownership = ownership
        self._client: docker.DockerClient | None = None
        self._container_spec: str | None = None
        self._init_lock = asyncio.Lock()
        self._resource_lock = threading.RLock()
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._user_guards: dict[int, LifecycleGuard] = {}
        self._conversation_guards: dict[tuple[int, UUID], LifecycleGuard] = {}
        self._mutation_locks: dict[tuple[int, UUID], threading.RLock] = {}
        self._start_locks: dict[int, threading.Lock] = {}
        self._activity_lock = threading.Lock()
        self._last_activity: dict[int, float] = {}
        self._last_persisted_activity: dict[int, float] = {}
        self._capacity = FairCapacityLimiter(
            max_running=sandbox_config.max_running_containers,
            max_waiting=sandbox_config.max_capacity_waiters,
            wait_timeout_seconds=sandbox_config.capacity_wait_timeout_seconds,
        )
        self._health_lock = threading.Lock()
        self._last_cleanup_started_at: float | None = None
        self._last_cleanup_completed_at: float | None = None
        self._cleanup_consecutive_failures = 0
        self._cleanup_last_error: str | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._ownership_started = False

    def _get_client(self) -> docker.DockerClient:
        """获取已初始化的 Docker 客户端"""
        if self._client is None:
            raise RuntimeError("Docker 沙箱管理器尚未初始化")
        return self._client

    def _init_sync(self) -> None:
        """连接 Docker 并加载沙盒镜像"""
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
            if self._config.workspace_quota_mode == "application":
                logger.warning(
                    "Docker 沙箱使用应用层强制的工作区配额；宿主机磁盘占用可能临时超过设定限制"
                )
            if image.id is None:
                raise RuntimeError("Docker 沙箱镜像缺少不可变 ID")
            self._container_spec = self._container_spec_digest(image.id)
        except Exception:
            client.close()
            raise
        self._client = client

    async def init(self, *, start_cleanup: bool = True) -> None:
        """初始化 Docker 沙盒管理器"""
        async with self._init_lock:
            if not self._ownership_started:
                await asyncio.to_thread(self._ownership.start_runtime)
                self._ownership_started = True
            if self._client is None:
                try:
                    await asyncio.to_thread(self._init_sync)
                    await asyncio.to_thread(self._reconcile_running_containers_sync)
                except Exception:
                    with self._ownership.release_runtime():
                        pass
                    self._ownership_started = False
                    raise
            if start_cleanup and self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(
                    self._cleanup_idle_containers()
                )

    async def _get_resources(
        self,
        user_id: int,
        conversation_id: UUID | None = None,
    ) -> tuple[
        asyncio.Lock,
        LifecycleGuard,
        threading.Lock,
        LifecycleGuard | None,
        threading.RLock | None,
    ]:
        """获取用户和会话的并发控制资源"""
        with self._resource_lock:
            user_lock = self._user_locks.setdefault(user_id, asyncio.Lock())
            user_guard = self._user_guards.setdefault(user_id, LifecycleGuard())
            start_lock = self._start_locks.setdefault(user_id, threading.Lock())
            conversation_guard = None
            mutation_lock = None
            if conversation_id is not None:
                conversation_guard = self._conversation_guards.setdefault(
                    (user_id, conversation_id),
                    LifecycleGuard(),
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
        activity_at = time.time()
        with self._activity_lock:
            self._last_activity[user_id] = activity_at
        self._ownership.touch(user_id, activity_at)
        self._capacity.notify_waiters()

    def _last_activity_timestamp(self, user_id: int) -> float:
        """获取用户最近活动时间戳"""
        with self._activity_lock:
            local_activity = self._last_activity.get(user_id, 0.0)
        return max(local_activity, self._ownership.last_activity(user_id))

    def _idle_seconds(self, user_id: int) -> float:
        """获取用户沙盒持续空闲的秒数"""
        last_activity = self._last_activity_timestamp(user_id)
        if last_activity <= 0:
            return 0.0
        return max(0.0, time.time() - last_activity)

    def _container_name(self, user_id: int) -> str:
        """构造用户容器名称"""
        return f"dataagent-{self._config.deployment_namespace}-sandbox-user-{user_id}"

    def _volume_name(self, user_id: int) -> str:
        """构造用户数据卷名称"""
        return f"{self._container_name(user_id)}-data"

    def _resource_labels(self, user_id: int) -> dict[str, str]:
        """构造容器和卷的归属标签"""
        return {
            _DEPLOYMENT_LABEL: self._config.deployment_namespace,
            _USER_LABEL: str(user_id),
            _QUOTA_MODE_LABEL: self._config.workspace_quota_mode,
            _QUOTA_BYTES_LABEL: str(self._config.max_workspace_bytes),
        }

    def _container_filters(self) -> dict[str, str | list[str] | bool]:
        """构造当前部署实例的 Docker 资源过滤条件"""
        return {
            "label": [
                f"{_DEPLOYMENT_LABEL}={self._config.deployment_namespace}",
                _USER_LABEL,
            ]
        }

    def _volume_driver_options(self, user_id: int) -> dict[str, str]:
        """渲染用户卷驱动参数"""
        fields = {
            "deployment_namespace": self._config.deployment_namespace,
            "user_id": user_id,
            "max_workspace_bytes": self._config.max_workspace_bytes,
        }
        return {
            key: value.format_map(fields)
            for key, value in self._config.volume_driver_options.items()
        }

    def _runtime_container_spec(self) -> dict[str, Any]:
        """返回创建容器使用的完整运行规格"""
        return {
            "command": ["sleep", "infinity"],
            "init": True,
            "read_only": True,
            "user": "1000:1000",
            "working_dir": "/workspace",
            "tmpfs": {"/tmp": "rw,nosuid,nodev,size=256m"},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": self._config.memory_limit,
            "nano_cpus": self._config.nano_cpus,
            "pids_limit": self._config.pids_limit,
            "network_mode": self._config.network_mode,
            "environment": {"HOME": "/tmp"},
        }

    def _container_spec_digest(self, image_id: str) -> str:
        """计算完整容器运行和存储规格的稳定摘要"""
        spec_payload = {
            "layout_version": 4,
            "image_id": image_id,
            "runtime": self._runtime_container_spec(),
            "workspace_mount": {
                "target": "/workspace",
                "mode": "rw",
            },
            "volume": {
                "driver": self._config.volume_driver,
                "driver_options": self._config.volume_driver_options,
                "quota_mode": self._config.workspace_quota_mode,
                "quota_bytes": self._config.max_workspace_bytes,
            },
        }
        return hashlib.sha256(
            json.dumps(spec_payload, sort_keys=True).encode()
        ).hexdigest()

    def _get_or_create_volume(self, user_id: int):
        """获取用户数据卷并校验归属"""
        client = self._get_client()
        volume_name = self._volume_name(user_id)
        try:
            volume = client.volumes.get(volume_name)
        except NotFound:
            return client.volumes.create(
                name=volume_name,
                driver=self._config.volume_driver,
                driver_opts=self._volume_driver_options(user_id),
                labels=self._resource_labels(user_id),
            )
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

    def _create_container(self, user_id: int) -> Container:
        """创建保持停止状态的用户容器"""
        client = self._get_client()
        volume = self._get_or_create_volume(user_id)
        if self._container_spec is None:
            raise RuntimeError("Docker 沙箱容器配置不可用")

        container = client.containers.create(
            self._config.image,
            name=self._container_name(user_id),
            volumes={volume.name: {"bind": "/workspace", "mode": "rw"}},
            labels={
                **self._resource_labels(user_id),
                _CONTAINER_SPEC_LABEL: self._container_spec,
            },
            **self._runtime_container_spec(),
        )
        logger.info(f"创建已停止的用户 Docker 沙箱: user_id={user_id}")
        return container

    def _get_or_create_storage_container_sync(self, user_id: int) -> Container:
        """获取或创建容器，但不启动容器"""
        if user_id < 0:
            raise ValueError("user_id 不能为负数")
        name = self._container_name(user_id)
        container = self._get_existing_container_sync(user_id)
        if container is not None:
            if container.labels.get(_CONTAINER_SPEC_LABEL) == self._container_spec:
                return container
            logger.info(f"重建过期的 Docker 沙箱: user_id={user_id}")
            container.remove(force=True)
            self._mark_user_not_running(user_id)
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
        """获取已存在的用户容器"""
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

    def _mark_user_not_running(self, user_id: int) -> None:
        """释放用户占用的全局运行槽位"""
        self._capacity.mark_not_running(user_id)

    def _complete_running_reservation(
        self,
        user_id: int,
        *,
        running: bool,
    ) -> None:
        """完成或回滚运行槽位预留"""
        self._capacity.complete_reservation(user_id, running=running)

    def _running_containers_sync(self) -> list[tuple[int, Container]]:
        """读取 Docker 中当前部署的运行容器"""
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

    def _synchronize_capacity_sync(self) -> list[tuple[int, Container]]:
        """使用 Docker 实际状态刷新当前进程容量视图"""
        running = self._running_containers_sync()
        self._capacity.synchronize([user_id for user_id, _ in running])
        return running

    def _try_evict_idle_user_sync(self, user_id: int) -> bool:
        """尝试跨进程安全地停止一个空闲用户容器"""
        with self._resource_lock:
            user_guard = self._user_guards.setdefault(user_id, LifecycleGuard())
            start_lock = self._start_locks.setdefault(user_id, threading.Lock())
        with (
            self._ownership.user_maintenance(user_id),
            user_guard.try_maintenance() as acquired,
        ):
            if not acquired:
                return False
            with self._ownership.capacity(), start_lock:
                container = self._get_existing_container_sync(user_id)
                if container is not None and container.status == "running":
                    container.stop(timeout=10)
                    logger.info(
                        f"因容量限制停止最久未使用的 Docker 沙箱: user_id={user_id}"
                    )
                self._mark_user_not_running(user_id)
                return True

    def _reserve_running_slot_sync(
        self,
        user_id: int,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """等待并预留当前进程的运行容器槽位"""
        return self._capacity.acquire(
            user_id,
            self._last_activity_timestamp,
            self._try_evict_idle_user_sync,
            cancel_event,
        )

    def _get_running_container_sync(
        self,
        user_id: int,
        start_lock: threading.Lock,
        cancel_event: threading.Event | None = None,
    ) -> Container:
        """获取用户容器并在跨进程容量保护下按需启动"""
        deadline = time.monotonic() + self._config.capacity_wait_timeout_seconds
        while True:
            with self._ownership.capacity():
                self._synchronize_capacity_sync()

            reserved = self._reserve_running_slot_sync(user_id, cancel_event)
            retry = False
            try:
                with self._ownership.capacity(), start_lock:
                    if cancel_event is not None and cancel_event.is_set():
                        raise SandboxCapacityCancelledError("Docker 沙箱启动已取消")
                    container = self._get_or_create_storage_container_sync(user_id)
                    container.reload()
                    if container.status != "running":
                        running = self._running_containers_sync()
                        if len(running) >= self._config.max_running_containers:
                            retry = True
                        else:
                            container.start()
                            container.reload()
                            logger.info(f"启动 Docker 沙箱: user_id={user_id}")
                    if not retry:
                        if reserved:
                            self._complete_running_reservation(
                                user_id,
                                running=True,
                            )
                        else:
                            self._capacity.mark_running(user_id)
                        return container
            except Exception:
                if reserved:
                    self._complete_running_reservation(user_id, running=False)
                raise

            if reserved:
                self._complete_running_reservation(user_id, running=False)
            if time.monotonic() >= deadline:
                raise SandboxCapacityTimeoutError("等待跨进程 Docker 沙箱运行容量超时")
            if cancel_event is not None and cancel_event.wait(0.25):
                raise SandboxCapacityCancelledError("Docker 沙箱启动已取消")
            if cancel_event is None:
                time.sleep(0.25)

    def _reconcile_running_containers_sync(self) -> None:
        """启动时登记已有容器并安全收敛到运行上限"""
        containers = self._get_client().containers.list(
            all=True,
            filters=self._container_filters(),
        )
        running: list[tuple[int, Container]] = []
        for container in containers:
            raw_user_id = container.labels.get(_USER_LABEL)
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            with self._resource_lock:
                user_guard = self._user_guards.setdefault(
                    user_id,
                    LifecycleGuard(),
                )
                start_lock = self._start_locks.setdefault(
                    user_id,
                    threading.Lock(),
                )
            with (
                self._ownership.user_maintenance(user_id),
                user_guard.maintenance(),
            ):
                container.reload()
                activity_at = self._recover_activity_timestamp_sync(container)
                with self._activity_lock:
                    self._last_activity.setdefault(user_id, activity_at)
                    self._last_persisted_activity.setdefault(user_id, activity_at)
                if container.status == "running":
                    running.append((user_id, container))

        running.sort(
            key=lambda item: self._last_activity_timestamp(item[0]),
            reverse=True,
        )
        with self._ownership.capacity():
            self._synchronize_capacity_sync()
        for user_id, _ in running[self._config.max_running_containers :]:
            with self._resource_lock:
                user_guard = self._user_guards.setdefault(
                    user_id,
                    LifecycleGuard(),
                )
                start_lock = self._start_locks.setdefault(
                    user_id,
                    threading.Lock(),
                )
            with (
                self._ownership.user_maintenance(user_id),
                user_guard.maintenance(),
                self._ownership.capacity(),
                start_lock,
            ):
                current = self._get_existing_container_sync(user_id)
                if current is None or current.status != "running":
                    self._mark_user_not_running(user_id)
                    continue
                current_running = self._running_containers_sync()
                if len(current_running) <= self._config.max_running_containers:
                    self._capacity.synchronize(
                        [running_id for running_id, _ in current_running]
                    )
                    break
                current.stop(timeout=10)
                self._mark_user_not_running(user_id)
                logger.info(f"启动时停止超出上限的 Docker 沙箱: user_id={user_id}")

    @contextmanager
    def _open_archive_sync(
        self,
        container: Container,
        path: str,
    ) -> Generator[tarfile.TarFile, None, None]:
        """流式打开容器中的 archive，适用于运行或停止状态"""
        chunks, _ = container.get_archive(path)
        raw_reader = IteratorReader(iter(chunks))
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
                    f"文件大小超出限制: {member.size} > {max_bytes}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise FileNotFoundError(path)
            content = extracted.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise SandboxFileTooLargeError(f"文件大小超出限制: > {max_bytes}")
            return content, member

    @staticmethod
    def _parse_docker_timestamp(value: object) -> float:
        """解析 Docker 返回的 RFC3339 时间戳"""
        if not isinstance(value, str) or not value or value.startswith("0001-"):
            return 0.0
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.timestamp()
        except ValueError:
            return 0.0

    def _recover_activity_timestamp_sync(self, container: Container) -> float:
        """从持久文件或 Docker 状态恢复最近活动时间"""
        now = time.time()
        try:
            content, member = self._read_archive_file_sync(
                container,
                _SANDBOX_ACTIVITY_FILE,
                16 * 1024,
            )
            if member.uid != 0:
                raise RuntimeError("沙箱活动记录文件拥有者无效")
            payload = json.loads(content)
            if payload.get("version") != _ACTIVITY_FILE_VERSION:
                raise RuntimeError("不支持的沙箱活动记录文件版本")
            activity_at = float(payload["last_activity_at"])
            if 0 < activity_at <= now + 300:
                return activity_at
            raise RuntimeError("沙箱活动记录文件包含无效的时间戳")
        except (NotFound, FileNotFoundError):
            pass

        state = container.attrs.get("State") or {}
        candidates = [
            self._parse_docker_timestamp(state.get("StartedAt")),
            self._parse_docker_timestamp(state.get("FinishedAt")),
            self._parse_docker_timestamp(container.attrs.get("Created")),
        ]
        recovered = max(candidates, default=0.0)
        return min(recovered, now) if recovered > 0 else now

    def _persist_activity_sync(
        self,
        user_id: int,
        container: Container,
        *,
        force: bool = False,
    ) -> None:
        """将内存活动时间写入用户持久卷"""
        activity_at = self._last_activity_timestamp(user_id)
        with self._activity_lock:
            persisted_at = self._last_persisted_activity.get(user_id, 0.0)
        if activity_at <= 0 or not force and activity_at <= persisted_at:
            return
        content = json.dumps(
            {
                "version": _ACTIVITY_FILE_VERSION,
                "last_activity_at": activity_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._put_archive_sync(
            container,
            "/workspace",
            [],
            [
                (
                    PurePosixPath(_SANDBOX_ACTIVITY_FILE).name,
                    0,
                    0,
                    0o600,
                    io.BytesIO(content),
                    len(content),
                )
            ],
        )
        with self._activity_lock:
            self._last_persisted_activity[user_id] = activity_at

    def _put_archive_sync(
        self,
        container: Container,
        base_path: str,
        directories: list[tuple[str, int, int, int]],
        files: list[tuple[str, int, int, int, BinaryIO, int]],
    ) -> None:
        """构造受控 tar 并写入运行或停止容器"""
        with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as buffer:
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                for name, owner_uid, owner_gid, mode in directories:
                    info = tarfile.TarInfo(name=name.rstrip("/") + "/")
                    info.type = tarfile.DIRTYPE
                    info.mode = mode
                    info.uid = owner_uid
                    info.gid = owner_gid
                    archive.addfile(info)
                for name, owner_uid, owner_gid, mode, content, size in files:
                    info = tarfile.TarInfo(name=name)
                    info.size = size
                    info.mode = mode
                    info.uid = owner_uid
                    info.gid = owner_gid
                    archive.addfile(info, content)
            buffer.seek(0)
            if not container.put_archive(base_path, buffer):
                raise OSError(f"写入 Docker 归档失败: {base_path}")

    def _write_uid_registry_sync(
        self,
        container: Container,
        registry: SandboxUidRegistry,
    ) -> None:
        """将 UID 注册表持久化到用户数据卷"""
        content = json.dumps(
            {
                "version": _UID_REGISTRY_VERSION,
                "conversations": registry.conversations,
                "sessions": registry.sessions,
            },
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
                    0,
                    0o600,
                    io.BytesIO(content),
                    len(content),
                )
            ],
        )

    def _scan_workspace_uids_sync(
        self,
        container: Container,
    ) -> SandboxUidRegistry:
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
        return SandboxUidRegistry(conversations=mapping, sessions={})

    @staticmethod
    def _validate_session_registry_key(key: str) -> str:
        """校验并规范化 UID 注册表中的 Session 键"""
        parts = PurePosixPath(key).parts
        if len(parts) != 6 or parts[1] != "analyses" or parts[3] != "sessions":
            raise ValueError("沙盒 Session UID 键无效")
        conversation_id = str(UUID(parts[0]))
        scope = SandboxSessionScope(parts[2], parts[4], parts[5])
        return scope.registry_key(UUID(conversation_id))

    @staticmethod
    def _validate_uid_registry(registry: SandboxUidRegistry) -> None:
        """校验 conversation 和 Session UID 全局唯一"""
        values = [*registry.conversations.values(), *registry.sessions.values()]
        if len(values) != len(set(values)):
            raise RuntimeError("沙箱 UID 注册表包含重复的 UID")
        if any(
            uid < _MIN_CONVERSATION_UID or uid > _MAX_CONVERSATION_UID for uid in values
        ):
            raise RuntimeError("沙箱 UID 注册表包含无效的 UID")

    def _load_uid_registry_sync(
        self,
        container: Container,
    ) -> SandboxUidRegistry:
        """读取 UID 注册表，不存在时从已有工作区迁移"""
        migrated = False
        try:
            content, member = self._read_archive_file_sync(
                container,
                _SANDBOX_UID_REGISTRY,
                4 * 1024 * 1024,
            )
            if member.uid != 0:
                raise RuntimeError("沙箱 UID 注册表文件拥有者无效")
            payload = json.loads(content)
            version = payload.get("version")
            if version not in {1, _UID_REGISTRY_VERSION}:
                raise RuntimeError("不支持的沙箱 UID 注册表版本")
            raw_conversations = payload.get("conversations")
            raw_sessions = payload.get("sessions", {}) if version == 2 else {}
            if not isinstance(raw_conversations, dict) or not isinstance(
                raw_sessions,
                dict,
            ):
                raise TypeError("沙箱 UID 注册表格式无效")
            registry = SandboxUidRegistry(
                conversations={
                    str(UUID(key)): int(value)
                    for key, value in raw_conversations.items()
                },
                sessions={
                    self._validate_session_registry_key(key): int(value)
                    for key, value in raw_sessions.items()
                },
            )
            migrated = version == 1
        except (NotFound, FileNotFoundError):
            registry = self._scan_workspace_uids_sync(container)
            migrated = True
        self._validate_uid_registry(registry)
        if migrated:
            self._write_uid_registry_sync(container, registry)
        return registry

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
        raise RuntimeError("会话 UID 分配范围已耗尽")

    def _allocate_session_uid(
        self,
        registry_key: str,
        used_uids: set[int],
    ) -> int:
        """为 Agent Session 确定性分配未使用的 Linux UID"""
        uid_range = _MAX_CONVERSATION_UID - _MIN_CONVERSATION_UID + 1
        seed = f"session:{registry_key}".encode()
        for attempt in range(uid_range):
            digest = hashlib.blake2s(seed + attempt.to_bytes(8, "big")).digest()
            candidate = (
                _MIN_CONVERSATION_UID + int.from_bytes(digest[:8], "big") % uid_range
            )
            if candidate not in used_uids:
                return candidate
        raise RuntimeError("沙盒 UID 分配范围已耗尽")

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
                ("conversations", 0, 0, 0o711),
                (PurePosixPath(_SANDBOX_STAGING_ROOT).name, 0, 0, 0o700),
            ],
            [],
        )
        registry = self._load_uid_registry_sync(container)
        key = str(conversation_id)
        conversation_uid = registry.conversations.get(key)
        if conversation_uid is None:
            conversation_uid = self._allocate_conversation_uid(
                conversation_id,
                {
                    *registry.conversations.values(),
                    *registry.sessions.values(),
                },
            )
            registry.conversations[key] = conversation_uid
            self._write_uid_registry_sync(container, registry)

        target_path = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        existing = self._inspect_archive_path_sync(container, target_path)
        if existing is not None and (
            not existing.isdir() or existing.uid != conversation_uid
        ):
            raise RuntimeError("对话工作区所有者与 UID 注册表不一致")

        conversation_name = str(conversation_id)
        self._put_archive_sync(
            container,
            _SANDBOX_WORKSPACE_ROOT,
            [
                (conversation_name, conversation_uid, conversation_uid, 0o750),
                (
                    f"{conversation_name}/.home",
                    conversation_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{conversation_name}/.cache",
                    conversation_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{conversation_name}/.cache/uv",
                    conversation_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{conversation_name}/.tmp",
                    conversation_uid,
                    conversation_uid,
                    0o700,
                ),
            ],
            [],
        )
        self._put_archive_sync(
            container,
            _SANDBOX_STAGING_ROOT,
            [
                (conversation_name, 0, 0, 0o700),
                (f"{conversation_name}/{conversation_uid}", 0, 0, 0o700),
            ],
            [],
        )
        return conversation_uid

    def _ensure_session_workspace_archive_sync(
        self,
        container: Container,
        conversation_id: UUID,
        scope: SandboxSessionScope,
    ) -> tuple[int, int]:
        """创建 Agent Session 目录并返回 conversation/session UID"""
        conversation_uid = self._ensure_workspace_archive_sync(
            container,
            conversation_id,
        )
        registry = self._load_uid_registry_sync(container)
        registry_key = scope.registry_key(conversation_id)
        session_uid = registry.sessions.get(registry_key)
        if session_uid is None:
            session_uid = self._allocate_session_uid(
                registry_key,
                {
                    *registry.conversations.values(),
                    *registry.sessions.values(),
                },
            )
            registry.sessions[registry_key] = session_uid
            self._write_uid_registry_sync(container, registry)

        conversation_name = str(conversation_id)
        session_relative = scope.relative_workspace
        session_path = posixpath.join(
            _SANDBOX_WORKSPACE_ROOT,
            conversation_name,
            session_relative,
        )
        existing = self._inspect_archive_path_sync(container, session_path)
        if existing is not None and (
            not existing.isdir()
            or existing.uid not in {conversation_uid, session_uid}
            or existing.gid != conversation_uid
        ):
            raise RuntimeError("Agent Session 工作区所有者无效")

        analysis_root = f"analyses/{scope.analysis_id}"
        sessions_root = f"{analysis_root}/sessions"
        agent_root = f"{sessions_root}/{scope.agent_type}"
        self._put_archive_sync(
            container,
            f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_name}",
            [
                ("analyses", conversation_uid, conversation_uid, 0o750),
                (analysis_root, conversation_uid, conversation_uid, 0o750),
                (sessions_root, conversation_uid, conversation_uid, 0o750),
                (agent_root, conversation_uid, conversation_uid, 0o750),
                (session_relative, session_uid, conversation_uid, 0o750),
                (
                    f"{session_relative}/.home",
                    session_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{session_relative}/.cache",
                    session_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{session_relative}/.cache/uv",
                    session_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{session_relative}/.tmp",
                    session_uid,
                    conversation_uid,
                    0o700,
                ),
            ],
            [],
        )
        self._put_archive_sync(
            container,
            posixpath.join(_SANDBOX_STAGING_ROOT, conversation_name),
            [(str(session_uid), 0, 0, 0o700)],
            [],
        )
        prepared = self._inspect_archive_path_sync(container, session_path)
        if (
            prepared is None
            or not prepared.isdir()
            or prepared.uid != session_uid
            or prepared.gid != conversation_uid
            or prepared.mode & 0o777 != 0o750
        ):
            raise RuntimeError("Agent Session 工作区权限设置失败")
        return conversation_uid, session_uid

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
            raise RuntimeError("会话沙盒守卫不可用")

        def prepare() -> int:
            """在独占维护窗口中准备会话工作区"""
            with (
                self._ownership.user_maintenance(user_id),
                self._ownership.conversation_maintenance(
                    user_id,
                    conversation_id,
                ),
                self._ownership.user_mutation(user_id),
                user_guard.maintenance(),
                conversation_guard.maintenance(),
            ):
                self._ownership.assert_available(user_id, conversation_id)
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
            self._ownership,
            user_guard,
            conversation_guard,
            mutation_lock,
            lambda: self._touch_user(user_id),
            lambda cancel_event: self._get_running_container_sync(
                user_id,
                start_lock,
                cancel_event,
            ),
            self._capacity.notify_waiters,
        )
        return backend

    async def get_session_backend(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        agent_type: str,
        session_id: str,
    ) -> DockerSandboxBackend:
        """获取独立 Linux 身份的专业 Agent Session 后端"""
        await self.init()
        scope = SandboxSessionScope(analysis_id, agent_type, session_id)
        (
            user_lock,
            user_guard,
            start_lock,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            raise RuntimeError("会话沙盒守卫不可用")

        def prepare() -> tuple[int, int]:
            """在独占维护窗口中准备 Agent Session 工作区"""
            with (
                self._ownership.user_maintenance(user_id),
                self._ownership.conversation_maintenance(
                    user_id,
                    conversation_id,
                ),
                self._ownership.user_mutation(user_id),
                user_guard.maintenance(),
                conversation_guard.maintenance(),
            ):
                self._ownership.assert_available(user_id, conversation_id)
                container = self._get_or_create_storage_container_sync(user_id)
                return self._ensure_session_workspace_archive_sync(
                    container,
                    conversation_id,
                    scope,
                )

        async with user_lock:
            conversation_uid, session_uid = await asyncio.to_thread(prepare)
        self._touch_user(user_id)
        return DockerSandboxBackend(
            user_id,
            conversation_id,
            conversation_uid,
            self._config,
            self._ownership,
            user_guard,
            conversation_guard,
            mutation_lock,
            lambda: self._touch_user(user_id),
            lambda cancel_event: self._get_running_container_sync(
                user_id,
                start_lock,
                cancel_event,
            ),
            self._capacity.notify_waiters,
            session_scope=scope,
            execution_uid=session_uid,
        )

    @staticmethod
    def _registered_session_uid_for_path(
        registry: SandboxUidRegistry,
        conversation_id: UUID,
        relative_path: str,
    ) -> int | None:
        """返回与产物路径精确绑定的 Session UID"""
        parts = PurePosixPath(relative_path).parts
        if len(parts) < 3 or parts[0] != "analyses" or parts[2] != "sessions":
            return None
        if len(parts) < 6:
            raise SandboxPathError(relative_path)
        try:
            scope = SandboxSessionScope(parts[1], parts[3], parts[4])
        except ValueError as exc:
            raise SandboxPathError(relative_path) from exc
        session_uid = registry.sessions.get(scope.registry_key(conversation_id))
        if session_uid is None:
            raise SandboxPathError(relative_path)
        return session_uid

    @classmethod
    def _allowed_file_uids_for_path(
        cls,
        registry: SandboxUidRegistry,
        conversation_id: UUID,
        conversation_uid: int,
        relative_path: str,
    ) -> set[int]:
        """返回给定会话文件路径允许使用的属主 UID"""
        allowed = {conversation_uid}
        session_uid = cls._registered_session_uid_for_path(
            registry,
            conversation_id,
            relative_path,
        )
        if session_uid is not None:
            allowed.add(session_uid)
        return allowed

    def _validate_attachment_target_sync(
        self,
        container: Container,
        conversation_id: UUID,
        conversation_uid: int,
        relative_path: str,
    ) -> tuple[list[tuple[str, int, int, int]], int]:
        """校验附件路径中的每个组件并返回待创建目录和被替换大小"""
        workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        registry = self._load_uid_registry_sync(container)
        session_uid = self._registered_session_uid_for_path(
            registry,
            conversation_id,
            relative_path,
        )
        root_info = self._inspect_archive_path_sync(container, workspace)
        if (
            root_info is None
            or not root_info.isdir()
            or root_info.uid != conversation_uid
            or root_info.gid != conversation_uid
        ):
            raise OSError("对话工作区无效")

        parts = PurePosixPath(relative_path).parts
        directories: list[tuple[str, int, int, int]] = []
        current_path = workspace
        for index, component in enumerate(parts[:-1], start=1):
            current_path = posixpath.join(current_path, component)
            info = self._inspect_archive_path_sync(container, current_path)
            directory_uid = (
                session_uid
                if session_uid is not None and index >= 5
                else conversation_uid
            )
            if info is None:
                directories.append(
                    (
                        "/".join(parts[:index]),
                        directory_uid,
                        conversation_uid,
                        0o750,
                    )
                )
                continue
            allowed_uids = (
                {conversation_uid, session_uid}
                if session_uid is not None and index >= 5
                else {conversation_uid}
            )
            if (
                not info.isdir()
                or info.uid not in allowed_uids
                or info.gid != conversation_uid
            ):
                raise SandboxPathError(relative_path)

        target_path = posixpath.join(workspace, relative_path)
        target_info = self._inspect_archive_path_sync(container, target_path)
        if target_info is None:
            return directories, 0
        allowed_target_uids = {conversation_uid}
        if session_uid is not None:
            allowed_target_uids.add(session_uid)
        if (
            not target_info.isreg()
            or target_info.uid not in allowed_target_uids
            or target_info.gid != conversation_uid
        ):
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
        registry = self._load_uid_registry_sync(container)
        session_prefix = f"{conversation_id}/"
        allowed_uids = {
            conversation_uid,
            *(
                uid
                for key, uid in registry.sessions.items()
                if key.startswith(session_prefix)
            ),
        }
        total = 0
        with self._open_archive_sync(container, workspace) as archive:
            for member in archive:
                if member.isreg():
                    if member.uid not in allowed_uids or member.gid != conversation_uid:
                        raise OSError("对话工作区包含无效的所有者")
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
        user_guard: LifecycleGuard,
        conversation_guard: LifecycleGuard,
        mutation_lock: threading.RLock,
    ) -> None:
        """使用 Docker Archive API 上传附件，不启动容器"""
        with (
            self._ownership.user_maintenance(user_id),
            self._ownership.conversation_maintenance(
                user_id,
                conversation_id,
            ),
            self._ownership.user_mutation(user_id),
            user_guard.maintenance(),
            conversation_guard.maintenance(),
            mutation_lock,
        ):
            self._ownership.assert_available(user_id, conversation_id)
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
                    f"文件大小超出限制: {size} > {self._config.max_file_bytes}"
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
                    "工作区容量超出限制: "
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
                        conversation_uid,
                        0o640,
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
                raise OSError("上传附件未通过校验")

    def _download_attachment_sync(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        user_guard: LifecycleGuard,
        conversation_guard: LifecycleGuard,
    ) -> bytes:
        """使用 Docker Archive API 下载附件，不启动容器"""
        with (
            self._ownership.user_maintenance(user_id),
            self._ownership.conversation_maintenance(
                user_id,
                conversation_id,
            ),
            self._ownership.user_mutation(user_id),
            user_guard.maintenance(),
            conversation_guard.maintenance(),
        ):
            self._ownership.assert_available(user_id, conversation_id)
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
            registry = self._load_uid_registry_sync(container)
            allowed_uids = self._allowed_file_uids_for_path(
                registry,
                conversation_id,
                conversation_uid,
                normalized_path,
            )
            if member.uid not in allowed_uids or member.gid != conversation_uid:
                raise FileNotFoundError(normalized_path)
            return content

    async def _upload_normalized_file(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        content: BinaryIO,
    ) -> None:
        """将已校验路径的文件对象写入用户会话目录"""
        await self.init()
        (
            user_lock,
            user_guard,
            _,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            raise RuntimeError("会话沙盒守卫不可用")
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

    async def write_artifact(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> None:
        """写入可信系统分析产物"""
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
        """上传用户可变附件并返回规范化路径"""
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
        """下载用户会话目录中的文件"""
        normalized_path = normalize_attachment_path(path)
        await self.init()
        user_lock, user_guard, _, conversation_guard, _ = await self._get_resources(
            user_id,
            conversation_id,
        )
        if conversation_guard is None:
            raise RuntimeError("会话沙盒守卫不可用")
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

    async def delete_user_attachment(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> None:
        """删除用户可变附件"""
        normalized_path = normalize_user_attachment_path(path)
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
            """在独占维护窗口中检查会话文件"""
            with (
                self._ownership.user_maintenance(user_id),
                self._ownership.conversation_maintenance(
                    user_id,
                    conversation_id,
                ),
                self._ownership.user_mutation(user_id),
                user_guard.maintenance(),
                conversation_guard.maintenance(),
            ):
                self._ownership.assert_available(user_id, conversation_id)
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
                registry = self._load_uid_registry_sync(container)
                try:
                    allowed_uids = self._allowed_file_uids_for_path(
                        registry,
                        conversation_id,
                        conversation_uid,
                        normalized_path,
                    )
                except SandboxPathError:
                    return False
                return bool(
                    target is not None
                    and target.isreg()
                    and target.uid in allowed_uids
                    and target.gid == conversation_uid
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
            """删除会话工作区并更新 UID 注册表"""
            with (
                self._ownership.user_maintenance(user_id),
                self._ownership.conversation_maintenance(
                    user_id,
                    conversation_id,
                ),
                self._ownership.user_mutation(user_id),
                user_guard.maintenance(),
                conversation_guard.maintenance(allow_deleted=True),
                mutation_lock,
            ):
                self._ownership.mark_conversation_deleted(
                    user_id,
                    conversation_id,
                )
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
                    raise OSError(detail or "删除对话沙盒失败")
                registry = self._load_uid_registry_sync(container)
                registry.conversations.pop(str(conversation_id), None)
                session_prefix = f"{conversation_id}/"
                registry.sessions = {
                    key: value
                    for key, value in registry.sessions.items()
                    if not key.startswith(session_prefix)
                }
                self._write_uid_registry_sync(container, registry)

        async with user_lock:
            conversation_guard.mark_deleted()
            await asyncio.to_thread(delete)
        self._touch_user(user_id)

    async def delete_user_sandbox(self, user_id: int) -> None:
        """删除用户容器及其持久化数据卷"""
        await self.init()
        user_lock, user_guard, _, _, _ = await self._get_resources(user_id)

        def delete() -> None:
            """删除用户容器和持久化数据卷"""
            with (
                self._ownership.user_maintenance(user_id),
                self._ownership.user_mutation(user_id),
                self._ownership.capacity(),
                user_guard.maintenance(allow_deleted=True),
            ):
                self._ownership.mark_user_deleted(user_id)
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

        async with user_lock:
            user_guard.mark_deleted()
            self._capacity.cancel_user(user_id)
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
            self._last_persisted_activity.pop(user_id, None)
        await asyncio.to_thread(self._ownership.forget_user, user_id)

    def health(self) -> DockerSandboxHealth:
        """返回可供健康检查和监控采集的状态快照"""
        cleanup_task = self._cleanup_task
        with self._health_lock:
            return DockerSandboxHealth(
                cleanup_task_running=bool(
                    cleanup_task is not None and not cleanup_task.done()
                ),
                last_cleanup_started_at=self._last_cleanup_started_at,
                last_cleanup_completed_at=self._last_cleanup_completed_at,
                cleanup_consecutive_failures=self._cleanup_consecutive_failures,
                cleanup_last_error=self._cleanup_last_error,
                quota_mode=self._config.workspace_quota_mode,
                capacity=self._capacity.snapshot(),
            )

    def _record_cleanup_result(
        self,
        started_at: float,
        errors: list[str],
    ) -> None:
        """记录一次清理周期的健康状态"""
        with self._health_lock:
            self._last_cleanup_started_at = started_at
            self._last_cleanup_completed_at = time.time()
            if errors:
                self._cleanup_consecutive_failures += 1
                self._cleanup_last_error = errors[-1]
                failures = self._cleanup_consecutive_failures
            else:
                self._cleanup_consecutive_failures = 0
                self._cleanup_last_error = None
                failures = 0
        if failures >= self._config.cleanup_failure_alert_threshold:
            logger.error(
                f"Docker 沙箱清理连续失败: consecutive_failures={failures}, last_error={errors[-1]}"
            )

    async def _run_cleanup_cycle(self) -> None:
        """执行一个带用户级错误隔离的清理周期"""
        started_at = time.time()
        errors: list[str] = []
        try:
            with self._activity_lock:
                user_ids = set(self._last_activity)
            user_ids.update(await asyncio.to_thread(self._managed_user_ids_sync))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(f"资源发现失败: {exc}")
            logger.exception("发现 Docker 沙箱资源失败")
            self._record_cleanup_result(started_at, errors)
            return

        for user_id in user_ids:
            try:
                (
                    user_lock,
                    user_guard,
                    start_lock,
                    _,
                    _,
                ) = await self._get_resources(user_id)
                async with user_lock:
                    await asyncio.to_thread(
                        self._cleanup_idle_container_sync,
                        user_id,
                        user_guard,
                        start_lock,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"user_id={user_id}: {exc}")
                logger.exception(f"清理 Docker 沙箱失败: user_id={user_id}")
        self._record_cleanup_result(started_at, errors)

    async def _cleanup_idle_containers(self) -> None:
        """定期停止或删除空闲容器，并始终保留数据卷"""
        while True:
            try:
                await asyncio.sleep(self._config.cleanup_interval_seconds)
                await self._run_cleanup_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                started_at = time.time()
                error = f"清理循环失败: {exc}"
                logger.exception("Docker 沙箱清理循环异常")
                self._record_cleanup_result(started_at, [error])

    def _managed_user_ids_sync(self) -> set[int]:
        """列出 Docker 中已有的用户沙盒"""
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

    def _cleanup_idle_container_sync(
        self,
        user_id: int,
        user_guard: LifecycleGuard,
        start_lock: threading.Lock,
    ) -> None:
        """在没有活跃操作时停止或删除空闲用户容器"""
        with (
            self._ownership.user_maintenance(user_id),
            user_guard.try_maintenance() as acquired,
        ):
            if not acquired:
                return
            with self._ownership.capacity(), start_lock:
                container = self._get_existing_container_sync(user_id)
                if container is None:
                    return
                self._persist_activity_sync(user_id, container)
                idle_seconds = self._idle_seconds(user_id)
                if idle_seconds < self._config.idle_stop_seconds:
                    return
                if idle_seconds >= self._config.idle_remove_seconds:
                    container.remove(force=True)
                    self._mark_user_not_running(user_id)
                    with self._activity_lock:
                        self._last_activity.pop(user_id, None)
                        self._last_persisted_activity.pop(user_id, None)
                    logger.info(
                        f"删除空闲 Docker 沙箱并保留持久化数据卷: user_id={user_id}"
                    )
                    return
                if container.status == "running":
                    container.stop(timeout=10)
                    self._mark_user_not_running(user_id)
                    logger.info(f"停止空闲 Docker 沙箱: user_id={user_id}")

    def _finalize_containers_sync(self) -> None:
        """持久化活动时间并按配置停止运行中容器"""
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
            try:
                with self._resource_lock:
                    user_guard = self._user_guards.setdefault(
                        user_id,
                        LifecycleGuard(),
                    )
                    start_lock = self._start_locks.setdefault(
                        user_id,
                        threading.Lock(),
                    )
                with (
                    self._ownership.user_maintenance(user_id),
                    user_guard.maintenance(),
                    self._ownership.capacity(),
                    start_lock,
                ):
                    current = self._get_existing_container_sync(user_id)
                    if current is None:
                        continue
                    try:
                        self._persist_activity_sync(
                            user_id,
                            current,
                            force=True,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            f"持久化 Docker 沙箱活跃时间失败: container={current.name}"
                        )
                    current.reload()
                    if (
                        self._config.stop_containers_on_shutdown
                        and current.status == "running"
                    ):
                        current.stop(timeout=10)
                    self._mark_user_not_running(user_id)
            except NotFound:
                continue

    async def close(self) -> None:
        """停止后台任务并关闭 Docker 客户端"""
        await self._close(finalize_containers=True)

    async def disconnect(self) -> None:
        """释放短生命周期管理器且保留运行中的沙盒容器"""
        await self._close(finalize_containers=False)

    async def _close(self, *, finalize_containers: bool) -> None:
        """按调用场景释放 Docker 管理资源"""
        self._capacity.close()
        cleanup_task = self._cleanup_task
        self._cleanup_task = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
        client = self._client

        def release_runtime() -> None:
            """释放运行时租约并按需终止残留容器"""
            if not self._ownership_started:
                return
            with self._ownership.release_runtime() as last_runtime:
                if finalize_containers and last_runtime and client is not None:
                    self._finalize_containers_sync()

        try:
            await asyncio.to_thread(release_runtime)
        finally:
            self._ownership_started = False
            if client is not None:
                self._client = None
                await asyncio.to_thread(client.close)
            await asyncio.to_thread(self._ownership.close)
