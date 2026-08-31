"""沙箱进程内生命周期并发控制。"""

import threading
from collections.abc import Generator
from contextlib import contextmanager

from app.sandbox.exceptions import SandboxDeletedError


class LifecycleGuard:
    """协调并发操作与资源维护。"""

    def __init__(self) -> None:
        """初始化活动操作计数和维护删除状态。"""
        self._condition = threading.Condition()
        self._active_operations = 0
        self._maintenance = False
        self._deleted = False
        self._local = threading.local()

    @contextmanager
    def operation(self) -> Generator[None, None, None]:
        """进入可并发执行的资源操作。"""
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
                raise SandboxDeletedError("沙箱资源已被删除")
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
    def maintenance(
        self,
        *,
        allow_deleted: bool = False,
    ) -> Generator[None, None, None]:
        """独占资源并等待现有操作完成。"""
        if getattr(self._local, "operation_depth", 0):
            raise RuntimeError("无法在活跃操作期间启动维护流程")
        with self._condition:
            while self._maintenance:
                self._condition.wait()
            if self._deleted and not allow_deleted:
                raise SandboxDeletedError("沙箱资源已被删除")
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
    def try_maintenance(self) -> Generator[bool, None, None]:
        """仅在当前没有操作时尝试获取独占维护权。"""
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
        """获取当前活跃操作数。"""
        with self._condition:
            return self._active_operations

    def mark_deleted(self) -> None:
        """阻止资源继续接受新操作。"""
        with self._condition:
            self._deleted = True
            self._condition.notify_all()
