import asyncio
import os
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.sandbox.exceptions import SandboxDeletedError, SandboxOwnershipError
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.ownership import LocalSandboxOwnership, RedisSandboxOwnership
from tests.sandbox.test_docker_sandbox_manager import build_sandbox_config


def _assert_threads_stopped(*threads: threading.Thread) -> None:
    """等待测试线程退出，避免失败路径阻塞测试进程"""
    for thread in threads:
        if thread.ident is not None:
            thread.join(timeout=1)
    assert all(not thread.is_alive() for thread in threads)


def _redis_ownership(redis: MagicMock) -> RedisSandboxOwnership:
    """构造使用模拟 Redis 客户端的 ownership"""
    with patch("app.sandbox.ownership.Redis.from_url", return_value=redis):
        return RedisSandboxOwnership(
            "redis://localhost/0",
            "test",
            lock_timeout_seconds=10,
            wait_timeout_seconds=1,
            lease_seconds=3,
        )


def test_user_maintenance_waits_for_operations_and_blocks_new_operations() -> None:
    ownership = LocalSandboxOwnership()
    first_conversation = uuid4()
    second_conversation = uuid4()
    first_started = threading.Event()
    release_first = threading.Event()
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    second_started = threading.Event()

    def first_operation() -> None:
        with ownership.operation(7, first_conversation):
            first_started.set()
            release_first.wait(timeout=2)

    def maintenance() -> None:
        with ownership.user_maintenance(7):
            maintenance_started.set()
            release_maintenance.wait(timeout=2)

    def second_operation() -> None:
        with ownership.operation(7, second_conversation):
            second_started.set()

    first_thread = threading.Thread(target=first_operation, daemon=True)
    maintenance_thread = threading.Thread(target=maintenance, daemon=True)
    second_thread = threading.Thread(target=second_operation, daemon=True)
    try:
        first_thread.start()
        assert first_started.wait(timeout=1)
        maintenance_thread.start()
        assert not maintenance_started.wait(timeout=0.05)
        release_first.set()
        assert maintenance_started.wait(timeout=1)
        second_thread.start()
        assert not second_started.wait(timeout=0.05)
        release_maintenance.set()
        assert second_started.wait(timeout=1)
    finally:
        release_first.set()
        release_maintenance.set()
        _assert_threads_stopped(first_thread, maintenance_thread, second_thread)


def test_conversation_maintenance_only_waits_for_target_conversation() -> None:
    ownership = LocalSandboxOwnership()
    target_conversation = uuid4()
    other_conversation = uuid4()
    target_started = threading.Event()
    release_target = threading.Event()
    maintenance_started = threading.Event()
    other_started = threading.Event()

    def target_operation() -> None:
        with ownership.operation(7, target_conversation):
            target_started.set()
            release_target.wait(timeout=2)

    def maintenance() -> None:
        with ownership.conversation_maintenance(7, target_conversation):
            maintenance_started.set()

    def other_operation() -> None:
        with ownership.operation(7, other_conversation):
            other_started.set()

    target_thread = threading.Thread(target=target_operation, daemon=True)
    maintenance_thread = threading.Thread(target=maintenance, daemon=True)
    other_thread = threading.Thread(target=other_operation, daemon=True)
    try:
        target_thread.start()
        assert target_started.wait(timeout=1)
        maintenance_thread.start()
        assert not maintenance_started.wait(timeout=0.05)
        other_thread.start()
        assert other_started.wait(timeout=1)
        release_target.set()
        assert maintenance_started.wait(timeout=1)
    finally:
        release_target.set()
        _assert_threads_stopped(target_thread, maintenance_thread, other_thread)


def test_nested_operation_uses_one_ownership_slot() -> None:
    ownership = LocalSandboxOwnership()
    conversation_id = uuid4()

    with (
        ownership.operation(7, conversation_id),
        ownership.operation(7, conversation_id),
    ):
        assert ownership._active_users[7] == 1

    assert ownership._active_users[7] == 0


def test_activity_can_be_forgotten_after_user_deletion() -> None:
    ownership = LocalSandboxOwnership()
    ownership.touch(7, 123.0)
    assert ownership.last_activity(7) == 123.0

    ownership.forget_user(7)

    assert ownership.last_activity(7) == 0.0


class RuntimeOwnership(LocalSandboxOwnership):
    def __init__(self, *, last_runtime: bool) -> None:
        super().__init__()
        self._last_runtime = last_runtime

    @contextmanager
    def release_runtime(self):
        yield self._last_runtime


def test_manager_only_finalizes_containers_for_last_runtime() -> None:
    async def run_inline(operation, *args):
        return operation(*args)

    async def run(last_runtime: bool) -> int:
        ownership = RuntimeOwnership(last_runtime=last_runtime)
        manager = DockerSandboxManager(build_sandbox_config(), ownership, ())
        manager._client = MagicMock()
        manager._ownership_started = True
        with (
            patch.object(manager, "_finalize_containers_sync") as finalize,
            patch("app.sandbox.manager.asyncio.to_thread", side_effect=run_inline),
        ):
            await manager.close()
        return finalize.call_count

    assert asyncio.run(run(last_runtime=False)) == 0
    assert asyncio.run(run(last_runtime=True)) == 1


def test_local_runtime_release_marks_current_runtime_as_last() -> None:
    ownership = LocalSandboxOwnership()
    ownership.start_runtime()

    with ownership.release_runtime() as last_runtime:
        assert last_runtime


def test_redis_short_lock_does_not_start_renewal_thread() -> None:
    redis = MagicMock()
    lock = redis.lock.return_value
    lock.acquire.return_value = True
    ownership = _redis_ownership(redis)

    with (
        patch("app.sandbox.ownership.threading.Thread") as thread,
        ownership._short_lock("runtimes"),
    ):
        pass

    thread.assert_not_called()
    lock.extend.assert_not_called()
    lock.release.assert_called_once_with()


def test_redis_operation_registers_atomically_without_gate_locks() -> None:
    redis = MagicMock()
    redis.eval.return_value = 0
    redis.pipeline.return_value.execute.return_value = [1, 1]
    ownership = _redis_ownership(redis)
    ownership._runtime_stop = threading.Event()
    conversation_id = uuid4()

    with (
        patch("app.sandbox.ownership.threading.Thread") as thread,
        ownership.operation(7, conversation_id),
        ownership.operation(7, conversation_id),
    ):
        assert len(ownership._operation_leases) == 1

    thread.assert_not_called()
    redis.lock.assert_not_called()
    redis.eval.assert_called_once()
    assert not ownership._operation_leases


def test_redis_operation_waits_for_maintenance_before_registering() -> None:
    redis = MagicMock()
    redis.eval.side_effect = [3, 0]
    redis.pipeline.return_value.execute.return_value = [1, 1]
    ownership = _redis_ownership(redis)
    ownership._runtime_stop = threading.Event()

    with (
        patch("app.sandbox.ownership.time.sleep") as sleep,
        ownership.operation(7, uuid4()),
    ):
        pass

    sleep.assert_called_once_with(0.1)
    assert redis.eval.call_count == 2


def test_redis_operation_rejects_deleted_sandbox() -> None:
    redis = MagicMock()
    ownership = _redis_ownership(redis)
    ownership._runtime_stop = threading.Event()
    redis.eval.return_value = 1
    with (
        pytest.raises(SandboxDeletedError, match="用户沙箱已被删除"),
        ownership.operation(7, uuid4()),
    ):
        pass

    redis.eval.return_value = 2
    with (
        pytest.raises(SandboxDeletedError, match="会话沙箱已被删除"),
        ownership.operation(7, uuid4()),
    ):
        pass


def test_runtime_renews_all_registered_operation_leases() -> None:
    redis = MagicMock()
    pipe = redis.pipeline.return_value
    ownership = _redis_ownership(redis)
    ownership._operation_leases["operation-token"] = (
        "active-user-key",
        "active-conversation-key",
    )

    ownership._renew_leases()

    assert pipe.zadd.call_count == 3
    pipe.execute.assert_called_once_with()


def test_runtime_records_operation_renewal_failure() -> None:
    redis = MagicMock()
    redis.pipeline.return_value.execute.side_effect = RedisError("unavailable")
    ownership = _redis_ownership(redis)
    ownership._operation_leases["operation-token"] = (
        "active-user-key",
        "active-conversation-key",
    )

    ownership._renew_leases()

    assert ownership._operation_renewal_failures == {"operation-token"}
    with pytest.raises(SandboxOwnershipError, match="租约续期失败"):
        ownership._runtime_stop = threading.Event()
        redis.eval.return_value = 0
        redis.pipeline.return_value.execute.side_effect = None
        with patch("app.sandbox.ownership.uuid4") as make_uuid:
            make_uuid.return_value.hex = "operation-token"
            with ownership.operation(7, uuid4()):
                pass


@pytest.mark.skipif(
    os.getenv("RUN_REDIS_SANDBOX_TESTS") != "1",
    reason="set RUN_REDIS_SANDBOX_TESTS=1 to run Redis ownership tests",
)
def test_redis_maintenance_coordinates_independent_runtimes() -> None:
    namespace = f"ownership-test-{uuid4().hex}"

    def create_ownership() -> RedisSandboxOwnership:
        """构造共享测试命名空间的真实 Redis ownership"""
        return RedisSandboxOwnership(
            "redis://127.0.0.1:6379/15",
            namespace,
            lock_timeout_seconds=5,
            wait_timeout_seconds=3,
            lease_seconds=2,
        )

    first = create_ownership()
    second = create_ownership()
    conversation_id = uuid4()
    other_conversation_id = uuid4()
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    target_operation_started = threading.Event()
    active_operation_started = threading.Event()
    release_active_operation = threading.Event()
    waiting_maintenance_started = threading.Event()

    def maintain() -> None:
        """持有目标 Conversation 的维护窗口"""
        with first.conversation_maintenance(7, conversation_id):
            maintenance_started.set()
            release_maintenance.wait(timeout=2)

    def operate_on_target() -> None:
        """等待目标 Conversation 维护完成后进入操作"""
        with second.operation(7, conversation_id):
            target_operation_started.set()

    def hold_operation() -> None:
        """持有目标 Conversation 的活跃操作租约"""
        with first.operation(7, conversation_id):
            active_operation_started.set()
            release_active_operation.wait(timeout=2)

    def wait_for_operation() -> None:
        """等待目标 Conversation 活跃操作结束后进入维护"""
        with second.conversation_maintenance(7, conversation_id):
            waiting_maintenance_started.set()

    maintenance_thread = threading.Thread(target=maintain, daemon=True)
    operation_thread = threading.Thread(target=operate_on_target, daemon=True)
    active_operation_thread = threading.Thread(target=hold_operation, daemon=True)
    waiting_maintenance_thread = threading.Thread(
        target=wait_for_operation,
        daemon=True,
    )
    first.start_runtime()
    second.start_runtime()
    try:
        maintenance_thread.start()
        assert maintenance_started.wait(timeout=1)
        operation_thread.start()
        assert not target_operation_started.wait(timeout=0.2)
        with second.operation(7, other_conversation_id):
            pass
        release_maintenance.set()
        assert target_operation_started.wait(timeout=1)

        active_operation_thread.start()
        assert active_operation_started.wait(timeout=1)
        waiting_maintenance_thread.start()
        assert not waiting_maintenance_started.wait(timeout=0.2)
        release_active_operation.set()
        assert waiting_maintenance_started.wait(timeout=1)
    finally:
        release_maintenance.set()
        release_active_operation.set()
        _assert_threads_stopped(
            maintenance_thread,
            operation_thread,
            active_operation_thread,
            waiting_maintenance_thread,
        )
        with first.release_runtime():
            pass
        with second.release_runtime():
            pass
        keys = tuple(first._redis.scan_iter(f"dataagent:sandbox:{namespace}:*"))
        if keys:
            first._redis.delete(*keys)
        first.close()
        second.close()
