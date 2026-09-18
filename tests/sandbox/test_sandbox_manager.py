"""Docker 沙箱管理器测试。"""

import asyncio
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from docker.errors import NotFound

from app.sandbox.manager import DockerSandboxManager
from tests.sandbox.fakes import FakeSandboxOwnership, build_sandbox_config


def _manager() -> tuple[DockerSandboxManager, MagicMock, MagicMock]:
    """构造已经完成初始化的沙箱管理器。"""
    manager = DockerSandboxManager(
        build_sandbox_config(),
        FakeSandboxOwnership(),
        (),
    )
    client = MagicMock()
    archive = MagicMock()
    manager._client = client
    manager._container_spec = "test-spec"
    manager._ownership_started = True
    manager._archive = archive
    return manager, client, archive


def _stopped_container(manager: DockerSandboxManager) -> MagicMock:
    """构造启动后会更新运行状态的容器替身。"""
    container = MagicMock()
    container.status = "exited"
    container.labels = {
        **manager._resource_labels(7),
        "dataagent.sandbox.spec": "test-spec",
    }

    def start() -> None:
        container.status = "running"

    container.start.side_effect = start
    return container


def _delete_conversation(
    manager: DockerSandboxManager,
    conversation_id: UUID,
) -> None:
    """执行删除并关闭测试期间启动的后台清理任务。"""

    async def run_inline(operation, *args):
        return operation(*args)

    async def run() -> None:
        try:
            await manager.delete_conversation(7, conversation_id)
        finally:
            await manager.disconnect()

    with patch("app.sandbox.manager.asyncio.to_thread", side_effect=run_inline):
        asyncio.run(run())


def test_delete_conversation_starts_stopped_container() -> None:
    """已停止的用户容器会先启动，再执行会话目录删除。"""
    manager, client, archive = _manager()
    container = _stopped_container(manager)
    client.containers.get.return_value = container
    client.containers.list.return_value = []
    conversation_id = uuid4()

    _delete_conversation(manager, conversation_id)

    container.start.assert_called_once_with()
    archive.delete_conversation.assert_called_once_with(
        container,
        conversation_id,
    )


def test_delete_conversation_recreates_container_for_existing_volume() -> None:
    """容器已回收但卷仍存在时会重建运行容器完成删除。"""
    manager, client, archive = _manager()
    container = _stopped_container(manager)
    volume = MagicMock()
    volume.name = manager._volume_name(7)
    volume.attrs = {
        "Labels": manager._resource_labels(7),
        "Driver": manager._config.volume_driver,
        "Options": manager._volume_driver_options(7),
    }
    client.containers.get.side_effect = NotFound("missing")
    client.containers.create.return_value = container
    client.containers.list.return_value = []
    client.volumes.get.return_value = volume
    conversation_id = uuid4()

    _delete_conversation(manager, conversation_id)

    client.containers.create.assert_called_once()
    container.start.assert_called_once_with()
    archive.delete_conversation.assert_called_once_with(
        container,
        conversation_id,
    )


def test_delete_conversation_does_not_create_empty_storage() -> None:
    """容器和卷都不存在时删除保持幂等，不创建空沙箱。"""
    manager, client, archive = _manager()
    client.containers.get.side_effect = NotFound("missing")
    client.volumes.get.side_effect = NotFound("missing")

    _delete_conversation(manager, uuid4())

    client.containers.create.assert_not_called()
    client.volumes.create.assert_not_called()
    archive.delete_conversation.assert_not_called()


def test_init_cancellation_waits_for_thread_then_releases_resources() -> None:
    """初始化线程不能在取消后的清理之外继续创建 Docker 客户端。"""
    from threading import Event

    import pytest

    manager = DockerSandboxManager(build_sandbox_config(), FakeSandboxOwnership(), ())
    client = MagicMock()
    started, finish = Event(), Event()

    def initialize() -> None:
        started.set()
        assert finish.wait(5)
        manager._client = client
        manager._ownership_started = True

    async def run() -> None:
        task = asyncio.create_task(manager.init(start_cleanup=False))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        client.close.assert_called_once()
        assert manager._client is None
        assert not manager._ownership_started

    with patch.object(manager, "_initialize_runtime_sync", side_effect=initialize):
        asyncio.run(run())


def test_init_failure_closes_client_created_before_reconcile() -> None:
    """Docker 已连接但运行时校验失败时，客户端仍被关闭。"""
    import pytest

    manager = DockerSandboxManager(build_sandbox_config(), FakeSandboxOwnership(), ())
    client = MagicMock()

    def initialize() -> None:
        manager._client = client

    async def run() -> None:
        with pytest.raises(RuntimeError, match="reconcile"):
            await manager.init(start_cleanup=False)
        client.close.assert_called_once()
        assert manager._client is None
        assert not manager._ownership_started

    with (
        patch.object(manager, "_init_sync", side_effect=initialize),
        patch.object(
            manager._runtime_pool, "reconcile", side_effect=RuntimeError("reconcile")
        ),
    ):
        asyncio.run(run())
