"""Docker 沙箱管理器测试。"""

import asyncio
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from docker.errors import NotFound

from app.sandbox.application import DockerSandboxManager
from app.sandbox.contracts import SandboxArtifact, SandboxSessionScope
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


async def _run_inline(operation, *args):
    return operation(*args)


def _delete_conversation(
    manager: DockerSandboxManager,
    conversation_id: UUID,
) -> None:
    """执行删除并关闭测试期间启动的后台清理任务。"""

    async def run() -> None:
        try:
            await manager.delete_conversation(7, conversation_id)
        finally:
            await manager.disconnect()

    with patch(
        "app.sandbox.application.manager.asyncio.to_thread", side_effect=_run_inline
    ):
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


def test_write_artifact_returns_written_absolute_path() -> None:
    import io
    from unittest.mock import AsyncMock

    manager, _, _ = _manager()
    conversation_id = uuid4()
    content = io.BytesIO(b"data")
    with patch.object(
        manager, "_upload_normalized_file", new_callable=AsyncMock
    ) as upload:
        result = asyncio.run(
            manager.write_artifact(
                7,
                conversation_id,
                "./result.csv",
                content,
                session_scope=SandboxSessionScope("analysis", "analyst", "session"),
            )
        )
    assert (
        result
        == f"/data/{conversation_id}/sessions/analysis/analyst/session/result.csv"
    )
    upload.assert_awaited_once_with(
        7,
        conversation_id,
        "sessions/analysis/analyst/session/result.csv",
        content,
    )


def test_resolve_artifact_rejects_invalid_paths_without_storage_access() -> None:
    from unittest.mock import AsyncMock

    manager, client, archive = _manager()
    conversation_id = uuid4()
    paths = (
        f"/data/{uuid4()}/uploads/file.csv",
        f"/data/{conversation_id}",
        f"/data/{conversation_id}/private/file.csv",
        f"/data/{conversation_id}/uploads/../../file.csv",
        f"/data/{conversation_id}/sessions/a/analyst/b/.cache/file.csv",
    )
    with patch.object(manager, "init", new_callable=AsyncMock) as initialize:
        for path in paths:
            assert (
                asyncio.run(manager.resolve_artifacts(7, conversation_id, [path])) == {}
            )
    initialize.assert_not_awaited()
    client.containers.get.assert_not_called()
    archive.is_downloadable_file.assert_not_called()


def test_resolve_artifact_checks_storage_and_propagates_infrastructure_errors() -> None:
    from unittest.mock import AsyncMock

    import pytest

    manager, client, archive = _manager()
    conversation_id = uuid4()
    path = "sessions/analysis/analyst/session/result.csv"
    references = [path, f"/data/{conversation_id}/{path}"]
    with (
        patch.object(manager, "init", new_callable=AsyncMock),
        patch.object(manager, "_get_existing_container_sync", return_value=client),
        patch.object(manager, "_touch_user"),
        patch(
            "app.sandbox.application.manager.asyncio.to_thread", side_effect=_run_inline
        ),
    ):
        for available in (True, False):
            archive.is_downloadable_file.return_value = available
            assert asyncio.run(
                manager.resolve_artifacts(
                    7,
                    conversation_id,
                    references,
                )
            ) == (
                {
                    reference: SandboxArtifact(f"/data/{conversation_id}/{path}", path)
                    for reference in references
                }
                if available
                else {}
            )
        archive.is_downloadable_file.assert_called_with(client, conversation_id, path)
        assert archive.is_downloadable_file.call_count == 2
        archive.is_downloadable_file.side_effect = OSError("storage unavailable")
        with pytest.raises(OSError, match="storage unavailable"):
            asyncio.run(
                manager.resolve_artifacts(
                    7, conversation_id, [f"/data/{conversation_id}/{path}"]
                )
            )


def test_session_artifacts_share_one_scope_check_and_inspect_alias_once() -> None:
    from unittest.mock import AsyncMock

    manager, container, archive = _manager()
    conversation_id = uuid4()
    scope = SandboxSessionScope("analysis", "analyst", "session")
    absolute = scope.workspace_path(conversation_id) + "/result.csv"
    missing = scope.workspace_path(conversation_id) + "/missing.csv"
    references = [
        "result.csv",
        "./result.csv",
        absolute,
        "missing.csv",
        "../other/result.csv",
        f"/data/{uuid4()}/uploads/result.csv",
        ".cache/result.csv",
        ".",
        "result.csv",
    ]
    archive.is_downloadable_file.side_effect = lambda container, cid, path: (
        path.endswith("/result.csv")
    )
    with (
        patch.object(manager, "init", new_callable=AsyncMock) as initialize,
        patch.object(
            manager, "_get_existing_container_sync", return_value=container
        ) as lookup,
        patch.object(manager, "_touch_user"),
        patch(
            "app.sandbox.application.manager.asyncio.to_thread", side_effect=_run_inline
        ),
    ):
        resolved = asyncio.run(
            manager.resolve_artifacts(
                7,
                conversation_id,
                references,
                session_scope=scope,
            )
        )
    assert set(resolved) == {"result.csv", "./result.csv", absolute}
    assert all(item.path == absolute for item in resolved.values())
    assert all(
        item.relative_path == scope.relative_workspace + "/result.csv"
        for item in resolved.values()
    )
    assert missing not in {item.path for item in resolved.values()}
    initialize.assert_awaited_once()
    lookup.assert_called_once_with(7)
    assert archive.is_downloadable_file.call_count == 2


def test_write_artifact_cannot_escape_session() -> None:
    import io
    from unittest.mock import AsyncMock

    import pytest

    from app.sandbox.errors import SandboxPathError

    manager, _, _ = _manager()
    conversation_id = uuid4()
    scope = SandboxSessionScope("analysis", "analyst", "session")
    with patch.object(
        manager, "_upload_normalized_file", new_callable=AsyncMock
    ) as upload:
        for path in (
            "../other/result.csv",
            f"/data/{conversation_id}/uploads/result.csv",
            ".",
        ):
            with pytest.raises(SandboxPathError):
                asyncio.run(
                    manager.write_artifact(
                        7,
                        conversation_id,
                        path,
                        io.BytesIO(b"data"),
                        session_scope=scope,
                    )
                )
    upload.assert_not_awaited()


def test_attachment_operations_share_resolved_paths() -> None:
    import io
    from unittest.mock import AsyncMock

    manager, container, archive = _manager()
    conversation_id = uuid4()
    absolute = f"/data/{conversation_id}/uploads/report.csv"
    content = io.BytesIO(b"data")
    archive.download_file.return_value = b"data"
    with (
        patch.object(manager, "init", new_callable=AsyncMock),
        patch.object(
            manager, "_upload_normalized_file", new_callable=AsyncMock
        ) as upload,
        patch.object(manager, "_get_existing_container_sync", return_value=container),
        patch.object(
            manager, "_get_running_storage_container_sync", return_value=container
        ),
        patch.object(manager, "_touch_user"),
        patch(
            "app.sandbox.application.manager.asyncio.to_thread", side_effect=_run_inline
        ),
    ):
        for path in ("uploads/tmp/../report.csv", absolute):
            assert (
                asyncio.run(
                    manager.upload_user_attachment(7, conversation_id, path, content)
                )
                == "uploads/report.csv"
            )
            upload.assert_awaited_with(
                7, conversation_id, "uploads/report.csv", content
            )
            assert (
                asyncio.run(manager.download_file(7, conversation_id, path)) == b"data"
            )
            archive.download_file.assert_called_with(
                container, conversation_id, "uploads/report.csv"
            )
            asyncio.run(manager.delete_user_attachment(7, conversation_id, path))
            archive.delete_file.assert_called_with(
                container, conversation_id, "uploads/report.csv"
            )


def test_attachment_scope_is_checked_before_storage_access() -> None:
    import io
    from unittest.mock import AsyncMock

    import pytest

    from app.sandbox.errors import SandboxPathError

    manager, _, archive = _manager()
    conversation_id = uuid4()
    with (
        patch.object(manager, "init", new_callable=AsyncMock) as initialize,
        patch.object(
            manager, "_upload_normalized_file", new_callable=AsyncMock
        ) as upload,
    ):
        for path in ("../other/private.csv", f"/data/{uuid4()}/uploads/private.csv"):
            operations = [
                manager.upload_user_attachment(
                    7, conversation_id, path, io.BytesIO(b"data")
                ),
                manager.download_file(7, conversation_id, path),
                manager.delete_user_attachment(7, conversation_id, path),
            ]
            for operation in operations:
                with pytest.raises(SandboxPathError):
                    asyncio.run(operation)
    initialize.assert_not_awaited()
    upload.assert_not_awaited()
    assert not archive.mock_calls
