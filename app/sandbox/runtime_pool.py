"""Docker Container 运行容量与生命周期。"""

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from threading import Event

from docker.errors import NotFound
from docker.models.containers import Container
from loguru import logger

from app.sandbox.exceptions import SandboxCapacityUnavailableError
from app.sandbox.ownership import SandboxOwnership
from app.shared.config.app_config import SandboxConfig


class DockerRuntimePool:
    """用 Docker 实际运行状态管理启动容量和空闲回收。"""

    def __init__(
        self,
        config: SandboxConfig,
        ownership: SandboxOwnership,
        *,
        get_or_create_container: Callable[[int], Container],
        get_existing_container: Callable[[int], Container | None],
        running_containers: Callable[[], list[tuple[int, Container]]],
    ) -> None:
        """绑定 Container 存储操作和跨进程协调器。"""
        self._config = config
        self._ownership = ownership
        self._get_or_create_container = get_or_create_container
        self._get_existing_container = get_existing_container
        self._running_containers = running_containers

    def _activity_at(self, user_id: int) -> float:
        """读取 Redis 中唯一的用户活动时间。"""
        return self._ownership.last_activity(user_id)

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
                container = self._get_or_create_container(user_id)
            if container.status == "running":
                return container

            running = self._running_containers()
            if len(running) < self._config.max_running_containers:
                container.start()
                container.reload()
                logger.info(f"启动 Docker 沙箱: user_id={user_id}")
                return container
            candidates = [
                idle_user_id
                for idle_user_id, _ in sorted(
                    running,
                    key=lambda item: self._activity_at(item[0]),
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
                current = self._get_existing_container(idle_user_id)
                if (
                    current is None
                    or current.status != "running"
                    or self._ownership.is_user_active(idle_user_id)
                ):
                    continue
                running = self._running_containers()
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
                container = self._get_or_create_container(user_id)
            if container.status != "running":
                if (
                    len(self._running_containers())
                    >= self._config.max_running_containers
                ):
                    raise SandboxCapacityUnavailableError("Docker 沙箱运行容量已满")
                container.start()
                container.reload()
                logger.info(f"启动 Docker 沙箱: user_id={user_id}")
            return container

    def reconcile(self) -> None:
        """为已运行 Container 初始化活动记录并收敛既有容量。"""
        running = self._running_containers()
        for user_id, _ in running:
            if self._activity_at(user_id) <= 0:
                self._ownership.touch(user_id, time.time())
        running.sort(key=lambda item: self._activity_at(item[0]), reverse=True)
        for user_id, _ in running[self._config.max_running_containers :]:
            with self._ownership.user_maintenance(user_id), self._ownership.capacity():
                current = self._get_existing_container(user_id)
                if current is not None and current.status == "running":
                    current.stop(timeout=10)
                    logger.info(f"启动时停止超出上限的 Docker 沙箱: user_id={user_id}")

    def cleanup_idle(self, user_id: int) -> None:
        """在用户操作结束后回收闲置 Container，始终保留 Volume。"""
        with self._ownership.user_maintenance(user_id), self._ownership.capacity():
            container = self._get_existing_container(user_id)
            if container is None:
                return
            idle_seconds = max(0.0, time.time() - self._activity_at(user_id))
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

    def finalize(self, containers: list[Container]) -> None:
        """在最后一个应用运行时按配置停止 Container。"""
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
                current = self._get_existing_container(user_id)
                if (
                    current is not None
                    and self._config.stop_containers_on_shutdown
                    and current.status == "running"
                ):
                    current.stop(timeout=10)
