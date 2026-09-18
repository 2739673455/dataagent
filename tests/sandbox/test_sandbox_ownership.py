import asyncio
import threading
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.sandbox.exceptions import SandboxDeletedError, SandboxOwnershipError
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.ownership import RedisSandboxOwnership
from tests.sandbox.fakes import FakeSandboxOwnership, build_sandbox_config


def _redis_ownership(redis: MagicMock) -> RedisSandboxOwnership:
    """构造使用模拟 Redis 客户端的 ownership。"""
    with patch("app.sandbox.ownership.Redis.from_url", return_value=redis):
        return RedisSandboxOwnership(
            "redis://localhost/0",
            "test",
            lock_timeout_seconds=10,
            wait_timeout_seconds=1,
            lease_seconds=3,
        )


def test_manager_only_finalizes_containers_for_last_runtime() -> None:
    async def run_inline(operation, *args):
        return operation(*args)

    async def run(last_runtime: bool) -> int:
        ownership = FakeSandboxOwnership(last_runtime=last_runtime)
        manager = DockerSandboxManager(build_sandbox_config(), ownership, ())
        manager._client = MagicMock()
        manager._ownership_started = True
        with (
            patch.object(manager._runtime_pool, "finalize") as finalize,
            patch("app.sandbox.manager.asyncio.to_thread", side_effect=run_inline),
        ):
            await manager.close()
        return finalize.call_count

    assert asyncio.run(run(last_runtime=False)) == 0
    assert asyncio.run(run(last_runtime=True)) == 1


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
