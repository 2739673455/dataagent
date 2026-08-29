import asyncio
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.sandbox.manager import DockerSandboxManager
from app.sandbox.ownership import LocalSandboxOwnership
from tests.sandbox.test_docker_sandbox_manager import build_sandbox_config


def _assert_threads_stopped(*threads: threading.Thread) -> None:
    """等待测试线程退出，避免失败路径阻塞测试进程"""
    for thread in threads:
        if thread.ident is not None:
            thread.join(timeout=1)
    assert all(not thread.is_alive() for thread in threads)


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
