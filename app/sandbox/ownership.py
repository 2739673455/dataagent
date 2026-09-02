"""沙箱跨进程所有权协调。"""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from typing import Protocol, cast
from uuid import UUID, uuid4

from redis import Redis
from redis.exceptions import LockError, RedisError

from app.sandbox.exceptions import SandboxDeletedError, SandboxOwnershipError

_REGISTER_OPERATION_SCRIPT = """
if redis.call("exists", KEYS[1]) == 1 then
    return 1
end
if redis.call("exists", KEYS[2]) == 1 then
    return 2
end
if redis.call("exists", KEYS[3]) == 1 or redis.call("exists", KEYS[4]) == 1 then
    return 3
end
redis.call("zadd", KEYS[5], ARGV[1], ARGV[2])
redis.call("zadd", KEYS[6], ARGV[1], ARGV[2])
return 0
"""


class SandboxOwnership(Protocol):
    """沙箱运行时需要的跨进程协调接口。"""

    def start_runtime(self) -> None:
        """登记当前进程的沙箱运行时租约。"""
        ...

    @contextmanager
    def release_runtime(self) -> Generator[bool, None, None]:
        """释放运行时租约并返回是否已无存活运行时。"""
        ...

    @contextmanager
    def capacity(self) -> Generator[None, None, None]:
        """串行化沙箱容量检查与创建。"""
        ...

    @contextmanager
    def user_mutation(self, user_id: int) -> Generator[None, None, None]:
        """串行化指定用户的沙箱结构变更。"""
        ...

    def assert_available(
        self,
        user_id: int,
        conversation_id: UUID | None = None,
    ) -> None:
        """确认用户或会话沙箱未被标记删除。"""
        ...

    def mark_conversation_deleted(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """记录会话沙箱删除墓碑。"""
        ...

    def mark_user_deleted(self, user_id: int) -> None:
        """记录用户沙箱删除墓碑。"""
        ...

    @contextmanager
    def operation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> Generator[None, None, None]:
        """登记一个支持同线程重入的会话沙箱操作。"""
        ...

    @contextmanager
    def conversation_maintenance(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> Generator[None, None, None]:
        """等待会话操作结束并独占维护窗口。"""
        ...

    @contextmanager
    def user_maintenance(self, user_id: int) -> Generator[None, None, None]:
        """等待用户操作结束并独占维护窗口。"""
        ...

    def touch(self, user_id: int, activity_at: float) -> None:
        """更新用户沙箱的最后活动时间。"""
        ...

    def last_activity(self, user_id: int) -> float:
        """读取用户沙箱的最后活动时间。"""
        ...

    def is_user_active(self, user_id: int) -> bool:
        """检查用户是否仍有活跃操作租约。"""
        ...

    def forget_user(self, user_id: int) -> None:
        """清除用户沙箱的活动记录。"""
        ...

    def close(self) -> None:
        """关闭协调器持有的外部资源。"""
        ...


class RedisSandboxOwnership:
    """使用 Redis 协调同一部署中的 API 和 Celery 进程。"""

    def __init__(
        self,
        redis_url: str,
        deployment_namespace: str,
        *,
        lock_timeout_seconds: float,
        wait_timeout_seconds: float,
        lease_seconds: float = 30.0,
    ) -> None:
        """初始化 Redis 键空间、锁参数和运行时租约。"""
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._prefix = f"dataagent:sandbox:{deployment_namespace}"
        self._lock_timeout_seconds = lock_timeout_seconds
        self._wait_timeout_seconds = wait_timeout_seconds
        self._lease_seconds = lease_seconds
        self._local = threading.local()
        self._runtime_token = uuid4().hex
        self._runtime_stop: threading.Event | None = None
        self._runtime_renewal: threading.Thread | None = None
        self._operation_leases: dict[str, tuple[str, str]] = {}
        self._operation_renewal_failures: set[str] = set()
        self._operation_leases_lock = threading.Lock()

    def _key(self, suffix: str) -> str:
        """构造当前部署隔离的 Redis 键。"""
        return f"{self._prefix}:{suffix}"

    @contextmanager
    def _lock(self, suffix: str) -> Generator[None, None, None]:
        """获取支持后台续期的 Redis 分布式锁。"""
        lock = self._redis.lock(
            self._key(f"lock:{suffix}"),
            timeout=self._lock_timeout_seconds,
            blocking_timeout=self._wait_timeout_seconds,
            thread_local=False,
        )
        if not lock.acquire(blocking=True):
            raise SandboxOwnershipError(f"等待沙箱跨进程锁超时: {suffix}")
        stop = threading.Event()
        renewal_failed = threading.Event()

        def renew() -> None:
            """定期延长当前分布式锁的有效期。"""
            interval = max(1.0, self._lock_timeout_seconds / 3)
            while not stop.wait(interval):
                try:
                    lock.extend(self._lock_timeout_seconds, replace_ttl=True)
                except (LockError, RedisError):
                    renewal_failed.set()
                    return

        renewal = threading.Thread(
            target=renew,
            name="sandbox-lock-renewal",
            daemon=True,
        )
        renewal.start()
        try:
            yield
            if renewal_failed.is_set():
                raise SandboxOwnershipError(f"沙箱跨进程锁续期失败: {suffix}")
        finally:
            stop.set()
            renewal.join(timeout=2)
            with suppress(LockError):
                lock.release()

    @contextmanager
    def _short_lock(self, suffix: str) -> Generator[None, None, None]:
        """获取只保护短时 Redis 事务的分布式锁。"""
        lock = self._redis.lock(
            self._key(f"lock:{suffix}"),
            timeout=self._lock_timeout_seconds,
            blocking_timeout=self._wait_timeout_seconds,
            thread_local=False,
        )
        if not lock.acquire(blocking=True):
            raise SandboxOwnershipError(f"等待沙箱跨进程锁超时: {suffix}")
        try:
            yield
        finally:
            with suppress(LockError):
                lock.release()

    def _runtimes_key(self) -> str:
        """返回活跃沙箱运行时集合的 Redis 键。"""
        return self._key("active:runtimes")

    def _renew_leases(self) -> None:
        """续期当前运行时及其全部活跃操作。"""
        with self._operation_leases_lock:
            operation_leases = tuple(self._operation_leases.items())
            expires_at = time.time() + self._lease_seconds
            pipe = self._redis.pipeline(transaction=True)
            pipe.zadd(self._runtimes_key(), {self._runtime_token: expires_at})
            for token, (user_active_key, conversation_active_key) in operation_leases:
                pipe.zadd(user_active_key, {token: expires_at})
                pipe.zadd(conversation_active_key, {token: expires_at})
            try:
                pipe.execute()
            except RedisError:
                self._operation_renewal_failures.update(
                    token for token, _ in operation_leases
                )

    def _renew_runtime(self) -> None:
        """循环续期当前进程的运行时租约。"""
        stop = self._runtime_stop
        if stop is None:
            return
        interval = max(1.0, self._lease_seconds / 3)
        while not stop.wait(interval):
            self._renew_leases()

    def start_runtime(self) -> None:
        """登记当前进程并启动运行时租约续期线程。"""
        if self._runtime_stop is not None:
            return
        with self._short_lock("runtimes"):
            self._prune_active(self._runtimes_key())
            self._redis.zadd(
                self._runtimes_key(),
                {self._runtime_token: (time.time() + self._lease_seconds)},
            )
        self._runtime_stop = threading.Event()
        self._runtime_renewal = threading.Thread(
            target=self._renew_runtime,
            name="sandbox-runtime-renewal",
            daemon=True,
        )
        self._runtime_renewal.start()

    @contextmanager
    def release_runtime(self) -> Generator[bool, None, None]:
        """停止续期并释放当前进程的运行时租约。"""
        stop = self._runtime_stop
        renewal = self._runtime_renewal
        self._runtime_stop = None
        self._runtime_renewal = None
        if stop is not None:
            stop.set()
        if renewal is not None:
            renewal.join(timeout=2)
        with self._lock("runtimes"):
            self._redis.zrem(self._runtimes_key(), self._runtime_token)
            active_runtimes = self._prune_active(self._runtimes_key())
            yield active_runtimes == 0

    @contextmanager
    def capacity(self) -> Generator[None, None, None]:
        """通过分布式锁串行化全局容量操作。"""
        with self._lock("capacity"):
            yield

    @contextmanager
    def user_mutation(self, user_id: int) -> Generator[None, None, None]:
        """通过分布式锁串行化用户沙箱变更。"""
        with self._lock(f"user:{user_id}:mutation"):
            yield

    def _deleted_user_key(self, user_id: int) -> str:
        """构造用户删除墓碑键。"""
        return self._key(f"deleted:user:{user_id}")

    def _deleted_conversation_key(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> str:
        """构造会话删除墓碑键。"""
        return self._key(f"deleted:conversation:{user_id}:{conversation_id}")

    def assert_available(
        self,
        user_id: int,
        conversation_id: UUID | None = None,
    ) -> None:
        """从 Redis 墓碑检查用户和会话是否可用。"""
        keys = [self._deleted_user_key(user_id)]
        if conversation_id is not None:
            keys.append(self._deleted_conversation_key(user_id, conversation_id))
        deleted = self._redis.mget(keys)
        if not isinstance(deleted, list):
            raise SandboxOwnershipError("读取沙箱删除墓碑失败")
        if deleted[0] is not None:
            raise SandboxDeletedError("用户沙箱已被删除")
        if len(deleted) > 1 and deleted[1] is not None:
            raise SandboxDeletedError("会话沙箱已被删除")

    def mark_conversation_deleted(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """持久化会话删除墓碑。"""
        self._redis.set(
            self._deleted_conversation_key(user_id, conversation_id),
            "1",
        )

    def mark_user_deleted(self, user_id: int) -> None:
        """持久化用户删除墓碑。"""
        self._redis.set(self._deleted_user_key(user_id), "1")

    def _active_user_key(self, user_id: int) -> str:
        """构造用户活跃操作集合键。"""
        return self._key(f"active:user:{user_id}")

    def _active_conversation_key(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> str:
        """构造会话活跃操作集合键。"""
        return self._key(f"active:conversation:{user_id}:{conversation_id}")

    def _prune_active(self, key: str) -> int:
        """清除过期活动租约并返回剩余数量。"""
        now = time.time()
        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(key, "-inf", now)
        pipe.zcard(key)
        _, count = pipe.execute()
        return int(count)

    def _register_operation(
        self,
        user_id: int,
        conversation_id: UUID,
        token: str,
        user_active_key: str,
        conversation_active_key: str,
    ) -> None:
        """原子检查维护和删除状态并登记操作租约。"""
        deadline = time.monotonic() + self._wait_timeout_seconds
        keys = (
            self._deleted_user_key(user_id),
            self._deleted_conversation_key(user_id, conversation_id),
            self._key(f"lock:user:{user_id}:gate"),
            self._key(f"lock:conversation:{user_id}:{conversation_id}:gate"),
            user_active_key,
            conversation_active_key,
        )
        while True:
            try:
                status = int(
                    cast(
                        str | int,
                        self._redis.eval(
                            _REGISTER_OPERATION_SCRIPT,
                            len(keys),
                            *keys,
                            str(time.time() + self._lease_seconds),
                            token,
                        ),
                    )
                )
            except RedisError as exc:
                raise SandboxOwnershipError("登记沙箱操作租约失败") from exc
            if status == 0:
                return
            if status == 1:
                raise SandboxDeletedError("用户沙箱已被删除")
            if status == 2:
                raise SandboxDeletedError("会话沙箱已被删除")
            if status != 3:
                raise SandboxOwnershipError(f"登记沙箱操作返回未知状态: {status}")
            if time.monotonic() >= deadline:
                raise SandboxOwnershipError("等待沙箱维护结束超时")
            time.sleep(0.1)

    @contextmanager
    def operation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> Generator[None, None, None]:
        """登记并续期一个跨进程会话沙箱操作。"""
        key = (user_id, conversation_id)
        depths = getattr(self._local, "operation_depths", None)
        if depths is None:
            depths = {}
            self._local.operation_depths = depths
        if key in depths:
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return

        if self._runtime_stop is None:
            raise SandboxOwnershipError("沙箱运行时尚未启动")

        token = uuid4().hex
        user_active_key = self._active_user_key(user_id)
        conversation_active_key = self._active_conversation_key(
            user_id,
            conversation_id,
        )
        self._register_operation(
            user_id,
            conversation_id,
            token,
            user_active_key,
            conversation_active_key,
        )
        with self._operation_leases_lock:
            # 后台续租线程只读取该表；先登记 Redis 再公开本地租约，避免续租不存在的操作。
            self._operation_leases[token] = (
                user_active_key,
                conversation_active_key,
            )
        depths[key] = 1
        try:
            yield
            with self._operation_leases_lock:
                renewal_failed = token in self._operation_renewal_failures
            if renewal_failed:
                raise SandboxOwnershipError("沙箱操作租约续期失败")
        finally:
            depths.pop(key, None)
            with self._operation_leases_lock:
                self._operation_leases.pop(token, None)
                self._operation_renewal_failures.discard(token)
            pipe = self._redis.pipeline(transaction=True)
            pipe.zrem(user_active_key, token)
            pipe.zrem(conversation_active_key, token)
            pipe.execute()

    def _wait_for_idle(self, key: str, label: str) -> None:
        """等待指定活动租约集合清空。"""
        deadline = time.monotonic() + self._wait_timeout_seconds
        while self._prune_active(key):
            if time.monotonic() >= deadline:
                raise SandboxOwnershipError(f"等待沙箱操作结束超时: {label}")
            time.sleep(0.1)

    def is_user_active(self, user_id: int) -> bool:
        """返回用户是否仍有未过期的操作租约。"""
        return self._prune_active(self._active_user_key(user_id)) > 0

    @contextmanager
    def conversation_maintenance(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> Generator[None, None, None]:
        """独占会话入口并等待跨进程操作结束。"""
        label = f"conversation:{user_id}:{conversation_id}"
        with self._lock(f"{label}:gate"):
            self._wait_for_idle(
                self._active_conversation_key(user_id, conversation_id),
                label,
            )
            yield

    @contextmanager
    def user_maintenance(self, user_id: int) -> Generator[None, None, None]:
        """独占用户入口并等待跨进程操作结束。"""
        label = f"user:{user_id}"
        with self._lock(f"{label}:gate"):
            self._wait_for_idle(self._active_user_key(user_id), label)
            yield

    def touch(self, user_id: int, activity_at: float) -> None:
        """将用户最后活动时间写入 Redis。"""
        self._redis.set(self._key(f"activity:{user_id}"), activity_at)

    def last_activity(self, user_id: int) -> float:
        """从 Redis 读取用户最后活动时间。"""
        value = self._redis.get(self._key(f"activity:{user_id}"))
        if isinstance(value, (bytes, str, int, float)):
            return float(value)
        return 0.0

    def forget_user(self, user_id: int) -> None:
        """删除 Redis 中的用户活动记录。"""
        self._redis.delete(self._key(f"activity:{user_id}"))

    def close(self) -> None:
        """关闭 Redis 客户端连接。"""
        self._redis.close()
