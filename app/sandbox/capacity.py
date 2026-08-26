"""沙箱运行容器容量调度"""

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from app.sandbox.exceptions import (
    SandboxCapacityCancelledError,
    SandboxCapacityClosedError,
    SandboxCapacityQueueFullError,
    SandboxCapacityTimeoutError,
)

_CAPACITY_WAIT_POLL_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class SandboxCapacitySnapshot:
    """沙箱容量调度状态快照"""

    running: int
    reserved: int
    waiting: int
    max_running: int
    max_waiting: int
    closed: bool


@dataclass(eq=False, slots=True)
class _CapacityWaiter:
    """公平容量队列中的等待项"""

    user_id: int
    deadline: float
    cancel_event: threading.Event | None
    cancelled: bool = False


class FairCapacityLimiter:
    """提供有界 FIFO 等待、超时和取消的运行容器容量限制器"""

    def __init__(
        self,
        max_running: int,
        max_waiting: int,
        wait_timeout_seconds: float,
    ) -> None:
        """初始化容量上限、公平等待队列和超时参数"""
        self._max_running = max_running
        self._max_waiting = max_waiting
        self._wait_timeout_seconds = wait_timeout_seconds
        self._condition = threading.Condition()
        self._running_users: set[int] = set()
        self._reserved_users: set[int] = set()
        self._waiters: deque[_CapacityWaiter] = deque()
        self._closed = False

    def _remove_waiter_unlocked(self, waiter: _CapacityWaiter) -> None:
        """从等待队列移除指定项并唤醒其他等待者"""
        try:
            self._waiters.remove(waiter)
        except ValueError:
            return
        self._condition.notify_all()

    def acquire(
        self,
        user_id: int,
        idle_priority: Callable[[int], float],
        evict_idle_user: Callable[[int], bool],
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """公平等待运行槽位并返回是否创建了新预留"""
        waiter: _CapacityWaiter | None = None
        deadline = time.monotonic() + self._wait_timeout_seconds
        try:
            while True:
                candidates: list[int] = []
                with self._condition:
                    if self._closed:
                        raise SandboxCapacityClosedError("Docker 沙箱容量限制器已关闭")
                    if (
                        waiter is not None
                        and waiter.cancelled
                        or cancel_event is not None
                        and cancel_event.is_set()
                    ):
                        raise SandboxCapacityCancelledError(
                            "Docker 沙箱容量排队等待已取消"
                        )
                    if user_id in self._running_users:
                        return False
                    if waiter is None:
                        occupied = len(self._running_users) + len(self._reserved_users)
                        if occupied < self._max_running and not self._waiters:
                            self._reserved_users.add(user_id)
                            return True
                        if len(self._waiters) >= self._max_waiting:
                            raise SandboxCapacityQueueFullError(
                                "Docker 沙箱容量等待队列已满"
                            )
                        waiter = _CapacityWaiter(
                            user_id=user_id,
                            deadline=deadline,
                            cancel_event=cancel_event,
                        )
                        self._waiters.append(waiter)

                    remaining = waiter.deadline - time.monotonic()
                    if remaining <= 0:
                        raise SandboxCapacityTimeoutError(
                            "等待 Docker 沙箱运行容量超时"
                        )
                    is_head = bool(self._waiters and self._waiters[0] is waiter)
                    occupied = len(self._running_users) + len(self._reserved_users)
                    if is_head and occupied < self._max_running:
                        self._waiters.popleft()
                        waiter = None
                        self._reserved_users.add(user_id)
                        self._condition.notify_all()
                        return True
                    if is_head:
                        candidates = sorted(
                            self._running_users,
                            key=idle_priority,
                        )

                if candidates and any(
                    evict_idle_user(candidate) for candidate in candidates
                ):
                    continue
                with self._condition:
                    if waiter is None:
                        continue
                    remaining = waiter.deadline - time.monotonic()
                    if remaining <= 0:
                        continue
                    self._condition.wait(
                        timeout=min(_CAPACITY_WAIT_POLL_SECONDS, remaining)
                    )
        finally:
            if waiter is not None:
                with self._condition:
                    self._remove_waiter_unlocked(waiter)

    def complete_reservation(self, user_id: int, *, running: bool) -> None:
        """提交或回滚一个运行槽位预留"""
        with self._condition:
            self._reserved_users.discard(user_id)
            if running:
                self._running_users.add(user_id)
            else:
                self._running_users.discard(user_id)
            self._condition.notify_all()

    def mark_running(self, user_id: int) -> None:
        """登记已经运行的用户容器"""
        with self._condition:
            self._running_users.add(user_id)
            self._condition.notify_all()

    def mark_not_running(self, user_id: int) -> None:
        """释放用户占用的运行槽位"""
        with self._condition:
            self._running_users.discard(user_id)
            self._reserved_users.discard(user_id)
            self._condition.notify_all()

    def synchronize(self, running_user_ids: list[int]) -> None:
        """使用 Docker 实际状态刷新当前进程的运行集合"""
        with self._condition:
            self._running_users = set(running_user_ids)
            self._reserved_users.difference_update(self._running_users)
            self._condition.notify_all()

    def reconcile(self, running_user_ids: list[int]) -> list[int]:
        """登记已有运行容器并返回超过上限的用户"""
        keep = running_user_ids[: self._max_running]
        overflow = running_user_ids[self._max_running :]
        with self._condition:
            self._running_users = set(keep)
            self._reserved_users.clear()
            self._condition.notify_all()
        return overflow

    def cancel_user(self, user_id: int) -> None:
        """取消指定用户的全部容量等待"""
        with self._condition:
            for waiter in self._waiters:
                if waiter.user_id == user_id:
                    waiter.cancelled = True
            self._condition.notify_all()

    def notify_waiters(self) -> None:
        """唤醒等待线程以重新检查取消和容量状态"""
        with self._condition:
            self._condition.notify_all()

    def close(self) -> None:
        """关闭容量限制器并取消全部等待"""
        with self._condition:
            self._closed = True
            for waiter in self._waiters:
                waiter.cancelled = True
            self._condition.notify_all()

    def snapshot(self) -> SandboxCapacitySnapshot:
        """返回当前容量状态快照"""
        with self._condition:
            return SandboxCapacitySnapshot(
                running=len(self._running_users),
                reserved=len(self._reserved_users),
                waiting=len(self._waiters),
                max_running=self._max_running,
                max_waiting=self._max_waiting,
                closed=self._closed,
            )
