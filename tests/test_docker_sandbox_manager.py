import asyncio
import io
import os
import threading
import time
import unittest
from collections.abc import Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.agents.manager import AgentManager
from app.clients.docker_sandbox_manager import (
    DockerSandboxManager,
    SandboxCapacityCancelledError,
    SandboxCapacityQueueFullError,
    SandboxCapacityTimeoutError,
    SandboxDeletedError,
    SandboxFileTooLargeError,
    SandboxPathError,
    _FairCapacityLimiter,
    _LifecycleGuard,
    normalize_attachment_path,
    normalize_user_attachment_path,
)
from app.conf.app_config import SandboxConfig


def build_sandbox_config(**updates: object) -> SandboxConfig:
    values = {
        "deployment_namespace": "test",
        "image": "dataagent-sandbox:latest",
        "build_context": "docker/sandbox",
        "build_network_mode": "host",
        "rebuild_image": False,
        "node_version": "20.19.2",
        "node_download_base": "https://npmmirror.com/mirrors/node",
        "pypi_index_url": "https://mirrors.aliyun.com/pypi/simple",
        "npm_registry": "https://registry.npmmirror.com",
        "memory_limit": "512m",
        "nano_cpus": 1_000_000_000,
        "pids_limit": 64,
        "network_mode": "none",
        "max_output_bytes": 4 * 1024 * 1024,
        "max_capture_bytes": 5 * 1024 * 1024,
        "max_file_bytes": 6 * 1024 * 1024,
        "max_workspace_bytes": 24 * 1024 * 1024,
        "workspace_quota_mode": "application",
        "volume_driver": "local",
        "volume_driver_options": {},
        "idle_stop_seconds": 60,
        "idle_remove_seconds": 120,
        "cleanup_interval_seconds": 60,
        "cleanup_failure_alert_threshold": 3,
        "max_running_containers": 2,
        "max_capacity_waiters": 16,
        "capacity_wait_timeout_seconds": 2,
        "stop_containers_on_shutdown": True,
    }
    values.update(updates)
    return SandboxConfig.model_validate(values)


class NormalizeAttachmentPathTest(unittest.TestCase):
    def test_normalizes_valid_relative_path(self) -> None:
        self.assertEqual(
            normalize_attachment_path("reports/summary.csv"), "reports/summary.csv"
        )

    def test_rejects_ambiguous_or_unsafe_paths(self) -> None:
        for path in (
            "",
            "/absolute.csv",
            "../secret.csv",
            "reports/../secret.csv",
            "reports\\secret.csv",
            "line\nbreak.csv",
            "~/.ssh/id_rsa",
        ):
            with self.subTest(path=path), self.assertRaises(SandboxPathError):
                normalize_attachment_path(path)

    def test_rejects_oversized_component(self) -> None:
        with self.assertRaises(SandboxPathError):
            normalize_attachment_path(f"{'x' * 256}.csv")

    def test_user_attachment_rejects_system_analysis_root(self) -> None:
        for path in ("analyses", "analyses/report.csv", "analyses/nested/report.csv"):
            with self.subTest(path=path), self.assertRaises(SandboxPathError):
                normalize_user_attachment_path(path)

    def test_system_paths_and_nested_analysis_names_remain_valid(self) -> None:
        self.assertEqual(
            normalize_attachment_path("analyses/run/report.csv"),
            "analyses/run/report.csv",
        )
        self.assertEqual(
            normalize_user_attachment_path("uploads/analyses/report.csv"),
            "uploads/analyses/report.csv",
        )


class AttachmentCapabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_system_writer_allows_analysis_path(self) -> None:
        manager = DockerSandboxManager(build_sandbox_config())
        conversation_id = uuid4()
        content = io.BytesIO(b"artifact")
        writer = AsyncMock()

        with patch.object(manager, "_upload_normalized_file", writer):
            await manager.write_artifact(
                7,
                conversation_id,
                "analyses/run/report.csv",
                content,
            )

        writer.assert_awaited_once_with(
            7,
            conversation_id,
            "analyses/run/report.csv",
            content,
        )

    async def test_user_mutations_reject_analysis_path_before_io(self) -> None:
        manager = DockerSandboxManager(build_sandbox_config())
        conversation_id = uuid4()
        writer = AsyncMock()

        with (
            patch.object(manager, "_upload_normalized_file", writer),
            patch.object(manager, "get_backend", AsyncMock()) as get_backend,
        ):
            with self.assertRaises(SandboxPathError):
                await manager.upload_user_attachment(
                    7,
                    conversation_id,
                    "analyses/run/report.csv",
                    io.BytesIO(b"overwrite"),
                )
            with self.assertRaises(SandboxPathError):
                await manager.delete_user_attachment(
                    7,
                    conversation_id,
                    "analyses/run/report.csv",
                )

        writer.assert_not_awaited()
        get_backend.assert_not_awaited()


class SandboxConfigTest(unittest.TestCase):
    def test_accepts_ordered_size_limits(self) -> None:
        config = build_sandbox_config()
        self.assertEqual(config.network_mode, "none")

    def test_rejects_inconsistent_size_limits(self) -> None:
        with self.assertRaises(ValidationError):
            build_sandbox_config(max_output_bytes=5 * 1024 * 1024 + 1)

    def test_rejects_non_positive_limits(self) -> None:
        with self.assertRaises(ValidationError):
            build_sandbox_config(max_file_bytes=0)

    def test_rejects_invalid_idle_thresholds(self) -> None:
        with self.assertRaises(ValidationError):
            build_sandbox_config(idle_stop_seconds=120, idle_remove_seconds=120)

    def test_rejects_invalid_deployment_namespace(self) -> None:
        with self.assertRaises(ValidationError):
            build_sandbox_config(deployment_namespace="Production One")

    def test_volume_driver_quota_requires_size_placeholder(self) -> None:
        with self.assertRaises(ValidationError):
            build_sandbox_config(
                workspace_quota_mode="volume_driver",
                volume_driver="quota-driver",
                volume_driver_options={"size": "1g"},
            )
        with self.assertRaises(ValidationError):
            build_sandbox_config(
                workspace_quota_mode="volume_driver",
                volume_driver="local",
                volume_driver_options={"size": "{max_workspace_bytes}"},
            )
        config = build_sandbox_config(
            workspace_quota_mode="volume_driver",
            volume_driver="quota-driver",
            volume_driver_options={"size": "{max_workspace_bytes}"},
        )
        self.assertEqual(config.volume_driver, "quota-driver")


class FairCapacityLimiterTest(unittest.TestCase):
    def test_queue_is_bounded_and_wait_can_be_cancelled(self) -> None:
        limiter = _FairCapacityLimiter(1, 1, 1)
        self.assertTrue(limiter.acquire(1, lambda _: 0, lambda _: False))
        limiter.complete_reservation(1, running=True)
        waiter_error: list[Exception] = []

        def wait_for_second_user() -> None:
            try:
                limiter.acquire(2, lambda _: 0, lambda _: False)
            except SandboxCapacityCancelledError as exc:
                waiter_error.append(exc)

        waiter_thread = threading.Thread(target=wait_for_second_user)
        waiter_thread.start()
        for _ in range(50):
            if limiter.snapshot().waiting == 1:
                break
            time.sleep(0.01)
        self.assertEqual(limiter.snapshot().waiting, 1)
        with self.assertRaises(SandboxCapacityQueueFullError):
            limiter.acquire(3, lambda _: 0, lambda _: False)
        limiter.cancel_user(2)
        waiter_thread.join(timeout=1)
        self.assertEqual(len(waiter_error), 1)
        self.assertIsInstance(waiter_error[0], SandboxCapacityCancelledError)

    def test_waiters_receive_capacity_in_fifo_order(self) -> None:
        limiter = _FairCapacityLimiter(1, 4, 2)
        self.assertTrue(limiter.acquire(1, lambda _: 0, lambda _: False))
        limiter.complete_reservation(1, running=True)
        acquired: list[int] = []
        release_second = threading.Event()

        def wait_for_user(user_id: int) -> None:
            reserved = limiter.acquire(user_id, lambda _: 0, lambda _: False)
            self.assertTrue(reserved)
            limiter.complete_reservation(user_id, running=True)
            acquired.append(user_id)
            if user_id == 2:
                release_second.wait(timeout=1)
            limiter.mark_not_running(user_id)

        second = threading.Thread(target=wait_for_user, args=(2,))
        third = threading.Thread(target=wait_for_user, args=(3,))
        second.start()
        for _ in range(50):
            if limiter.snapshot().waiting == 1:
                break
            time.sleep(0.01)
        third.start()
        for _ in range(50):
            if limiter.snapshot().waiting == 2:
                break
            time.sleep(0.01)
        limiter.mark_not_running(1)
        for _ in range(50):
            if acquired:
                break
            time.sleep(0.01)
        self.assertEqual(acquired, [2])
        release_second.set()
        second.join(timeout=1)
        third.join(timeout=1)
        self.assertEqual(acquired, [2, 3])

    def test_capacity_wait_times_out(self) -> None:
        limiter = _FairCapacityLimiter(1, 1, 0.05)
        self.assertTrue(limiter.acquire(1, lambda _: 0, lambda _: False))
        limiter.complete_reservation(1, running=True)
        with self.assertRaises(SandboxCapacityTimeoutError):
            limiter.acquire(2, lambda _: 0, lambda _: False)


class LifecycleGuardTest(unittest.TestCase):
    def test_maintenance_waits_for_active_operation(self) -> None:
        guard = _LifecycleGuard()
        operation_started = threading.Event()
        release_operation = threading.Event()
        maintenance_started = threading.Event()

        def operation() -> None:
            with guard.operation():
                operation_started.set()
                release_operation.wait(timeout=2)

        def maintenance() -> None:
            with guard.maintenance():
                maintenance_started.set()

        operation_thread = threading.Thread(target=operation)
        maintenance_thread = threading.Thread(target=maintenance)
        operation_thread.start()
        self.assertTrue(operation_started.wait(timeout=1))
        maintenance_thread.start()
        self.assertFalse(maintenance_started.wait(timeout=0.05))
        release_operation.set()
        self.assertTrue(maintenance_started.wait(timeout=1))
        operation_thread.join(timeout=1)
        maintenance_thread.join(timeout=1)

    def test_deleted_guard_rejects_new_operations(self) -> None:
        guard = _LifecycleGuard()
        with guard.maintenance():
            guard.mark_deleted()
        with self.assertRaises(SandboxDeletedError), guard.operation():
            pass


class DockerSandboxManagerPolicyTest(unittest.TestCase):
    def test_resource_names_and_volume_options_are_namespaced(self) -> None:
        from app.clients.docker_sandbox_manager import DockerSandboxManager

        manager = DockerSandboxManager(
            build_sandbox_config(
                deployment_namespace="production",
                workspace_quota_mode="volume_driver",
                volume_driver="quota-driver",
                volume_driver_options={
                    "size": "{max_workspace_bytes}",
                    "scope": "{deployment_namespace}-{user_id}",
                },
            )
        )
        self.assertEqual(
            manager._container_name(7),
            "dataagent-production-sandbox-user-7",
        )
        self.assertEqual(
            manager._volume_name(7),
            "dataagent-production-sandbox-user-7-data",
        )
        self.assertEqual(
            manager._volume_driver_options(7),
            {"size": str(24 * 1024 * 1024), "scope": "production-7"},
        )

    def test_runtime_spec_contains_security_boundaries(self) -> None:
        from app.clients.docker_sandbox_manager import DockerSandboxManager

        manager = DockerSandboxManager(build_sandbox_config())
        spec = manager._runtime_container_spec()
        self.assertTrue(spec["read_only"])
        self.assertEqual(spec["cap_drop"], ["ALL"])
        self.assertEqual(spec["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(spec["network_mode"], "none")
        self.assertEqual(spec["tmpfs"], {"/tmp": "rw,nosuid,nodev,size=256m"})

        original_digest = manager._container_spec_digest("sha256:image")
        changed_spec = {**spec, "tmpfs": {"/tmp": "rw,nosuid,nodev,size=128m"}}
        with patch.object(
            manager,
            "_runtime_container_spec",
            return_value=changed_spec,
        ):
            changed_digest = manager._container_spec_digest("sha256:image")
        self.assertNotEqual(original_digest, changed_digest)

    def test_health_exposes_cleanup_and_capacity_state(self) -> None:
        from app.clients.docker_sandbox_manager import DockerSandboxManager

        manager = DockerSandboxManager(build_sandbox_config())
        manager._record_cleanup_result(time.time(), ["docker unavailable"])
        health = manager.health()
        self.assertEqual(health.cleanup_consecutive_failures, 1)
        self.assertEqual(health.cleanup_last_error, "docker unavailable")
        self.assertEqual(health.capacity.max_waiting, 16)
        self.assertEqual(health.quota_mode, "application")


class DockerSandboxCleanupHealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_failure_is_recorded_and_next_cycle_recovers(self) -> None:
        from app.clients.docker_sandbox_manager import DockerSandboxManager

        manager = DockerSandboxManager(build_sandbox_config())

        async def run_inline(
            function: Callable[..., object],
            *args: object,
            **kwargs: object,
        ) -> object:
            return function(*args, **kwargs)

        with patch(
            "app.clients.docker_sandbox_manager.asyncio.to_thread",
            side_effect=run_inline,
        ):
            with (
                patch.object(
                    manager,
                    "_managed_user_ids_sync",
                    side_effect=RuntimeError("docker unavailable"),
                ),
                patch("app.clients.docker_sandbox_manager.logger.exception"),
            ):
                await manager._run_cleanup_cycle()
            self.assertEqual(manager.health().cleanup_consecutive_failures, 1)

            with patch.object(manager, "_managed_user_ids_sync", return_value=set()):
                await manager._run_cleanup_cycle()
        health = manager.health()
        self.assertEqual(health.cleanup_consecutive_failures, 0)
        self.assertIsNone(health.cleanup_last_error)


class AgentExecutionLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_delete_agent_cancels_active_execution(self) -> None:
        persistence_manager = MagicMock()
        persistence_manager.delete_thread = AsyncMock()
        store = MagicMock()
        store.aput = AsyncMock()
        persistence_manager.get_store.return_value = store

        @asynccontextmanager
        async def advisory_lock(*args: object, **kwargs: object):
            del args, kwargs
            yield

        persistence_manager.advisory_lock = advisory_lock
        manager = AgentManager(persistence_manager)
        bundle = MagicMock()
        bundle.planner_lock = advisory_lock
        bundle.session_service.planner_run = advisory_lock
        bundle.conversation_deleted = AsyncMock(return_value=False)
        user_id = 7
        conversation_id = uuid4()
        started = asyncio.Event()

        async def run() -> None:
            async with manager.execution(
                user_id,
                conversation_id,
                bundle=bundle,
            ):
                started.set()
                await asyncio.Future()

        run_task = asyncio.create_task(run())
        await asyncio.wait_for(started.wait(), timeout=1)
        await manager.delete_agent(user_id, conversation_id)

        self.assertTrue(run_task.cancelled())
        persistence_manager.delete_thread.assert_awaited_once()


@unittest.skipUnless(
    os.getenv("RUN_DOCKER_SANDBOX_TESTS") == "1",
    "set RUN_DOCKER_SANDBOX_TESTS=1 to run Docker integration tests",
)
class DockerSandboxIntegrationTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import docker
        from app.conf.app_config import ROOT_DIR

        config = build_sandbox_config()
        client = docker.from_env()
        try:
            client.images.build(
                path=str(ROOT_DIR / config.build_context),
                tag=config.image,
                rm=True,
                network_mode=config.build_network_mode,
                buildargs={
                    "NODE_VERSION": config.node_version,
                    "NODE_DOWNLOAD_BASE": config.node_download_base,
                    "PYPI_INDEX_URL": config.pypi_index_url,
                    "NPM_REGISTRY": config.npm_registry,
                },
            )
        finally:
            client.close()

    async def asyncSetUp(self) -> None:
        from app.clients.docker_sandbox_manager import DockerSandboxManager

        self.user_id = 2_000_000_000 + os.getpid()
        self.extra_user_ids: set[int] = set()
        self.manager = DockerSandboxManager(build_sandbox_config())
        await self.manager.init()

    async def asyncTearDown(self) -> None:
        for user_id in {self.user_id, *self.extra_user_ids}:
            await self.manager.delete_user_sandbox(user_id)
        await self.manager.close()

    async def test_backend_and_attachment_transfer_keep_container_stopped(self) -> None:
        conversation_id = uuid4()
        backend = await self.manager.get_backend(self.user_id, conversation_id)
        container = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(container)
        assert container is not None
        self.assertNotEqual(container.status, "running")
        self.assertEqual(container.labels["dataagent.sandbox.deployment"], "test")
        volume = self.manager._get_client().volumes.get(
            self.manager._volume_name(self.user_id)
        )
        volume.reload()
        self.assertEqual(
            volume.attrs["Labels"]["dataagent.sandbox.quota_mode"], "application"
        )

        await self.manager.upload_user_attachment(
            self.user_id,
            conversation_id,
            "uploads/input.csv",
            io.BytesIO(b"name,value\na,1\n"),
        )
        container.reload()
        self.assertNotEqual(container.status, "running")
        self.assertEqual(
            await self.manager.download_file(
                self.user_id,
                conversation_id,
                "uploads/input.csv",
            ),
            b"name,value\na,1\n",
        )
        self.assertTrue(
            await self.manager.is_file(
                self.user_id,
                conversation_id,
                "uploads/input.csv",
            )
        )
        container.reload()
        self.assertNotEqual(container.status, "running")

        self.assertEqual(backend.execute("printf started").output, "started")
        container.reload()
        self.assertEqual(container.status, "running")

    async def test_user_mutations_cannot_change_analysis_artifact(self) -> None:
        conversation_id = uuid4()
        artifact_path = "analyses/run/report.csv"
        await self.manager.write_artifact(
            self.user_id,
            conversation_id,
            artifact_path,
            io.BytesIO(b"verified artifact"),
        )

        with self.assertRaises(SandboxPathError):
            await self.manager.upload_user_attachment(
                self.user_id,
                conversation_id,
                artifact_path,
                io.BytesIO(b"user overwrite"),
            )
        with self.assertRaises(SandboxPathError):
            await self.manager.delete_user_attachment(
                self.user_id,
                conversation_id,
                artifact_path,
            )

        self.assertEqual(
            await self.manager.download_file(
                self.user_id,
                conversation_id,
                artifact_path,
            ),
            b"verified artifact",
        )
        await self.manager.delete_conversation(self.user_id, conversation_id)
        with self.assertRaises(SandboxDeletedError):
            await self.manager.download_file(
                self.user_id,
                conversation_id,
                artifact_path,
            )

    async def test_conversations_use_os_permission_isolation(self) -> None:
        first_id = uuid4()
        second_id = uuid4()
        first = await self.manager.get_backend(self.user_id, first_id)
        second = await self.manager.get_backend(self.user_id, second_id)
        self.assertNotEqual(
            first.execute(
                f"touch /workspace/.dataagent-staging/{first_id}/quota-bypass"
            ).exit_code,
            0,
        )
        self.assertIsNone(first.write("/first.txt", "first").error)
        self.assertIsNone(second.write("/data.txt", "TOP_SECRET_CONTENT").error)
        first_identity = first.execute("stat -c '%u %g %a' .").output.strip()
        second_identity = second.execute("stat -c '%u %g %a' .").output.strip()
        self.assertNotEqual(first_identity, second_identity)
        self.assertTrue(first_identity.endswith(" 700"))
        self.assertTrue(second_identity.endswith(" 700"))
        self.assertNotEqual(
            first.execute("cat /workspace/.dataagent-uids.json").exit_code,
            0,
        )

        response = first.execute(f"cat ../{second_id}/data.txt")

        self.assertNotEqual(response.exit_code, 0)
        self.assertNotIn("TOP_SECRET_CONTENT", response.output)

        self.assertEqual(
            first.execute(f"ln -s ../{second_id}/data.txt linked.txt").exit_code,
            0,
        )
        self.assertIsNotNone(first.read("/linked.txt").error)
        linked_download = first.download_files(["/linked.txt"])[0]
        self.assertIsNotNone(linked_download.error)
        self.assertNotIn(
            "TOP_SECRET_CONTENT",
            (linked_download.content or b"").decode(errors="replace"),
        )

        self.assertEqual(
            first.execute(f"ln -s ../{second_id} linked_directory").exit_code,
            0,
        )
        linked_directory_download = first.download_files(
            ["/linked_directory/data.txt"]
        )[0]
        self.assertIsNotNone(linked_directory_download.error)
        self.assertNotIn(
            "TOP_SECRET_CONTENT",
            (linked_directory_download.content or b"").decode(errors="replace"),
        )
        linked_upload = first.upload_files(
            [("/linked_directory/injected.txt", b"INJECTED_CONTENT")]
        )[0]
        self.assertIsNotNone(linked_upload.error)
        self.assertIsNotNone(second.read("/injected.txt").error)
        with self.assertRaises(SandboxPathError):
            await self.manager.upload_user_attachment(
                self.user_id,
                first_id,
                "linked_directory/http-injected.txt",
                io.BytesIO(b"HTTP_INJECTED_CONTENT"),
            )
        with self.assertRaises(SandboxPathError):
            await self.manager.download_file(
                self.user_id,
                first_id,
                "linked_directory/data.txt",
            )

    async def test_deployment_namespaces_do_not_share_resources(self) -> None:
        from app.clients.docker_sandbox_manager import DockerSandboxManager

        conversation_id = uuid4()
        first = await self.manager.get_backend(self.user_id, conversation_id)
        self.assertIsNone(first.write("/private.txt", "first-deployment").error)

        other_manager = DockerSandboxManager(
            build_sandbox_config(deployment_namespace="test-other")
        )
        await other_manager.init()
        try:
            second = await other_manager.get_backend(self.user_id, conversation_id)
            self.assertIsNotNone(second.read("/private.txt").error)
            self.assertNotEqual(
                self.manager._container_name(self.user_id),
                other_manager._container_name(self.user_id),
            )
            self.assertNotEqual(
                self.manager._volume_name(self.user_id),
                other_manager._volume_name(self.user_id),
            )
        finally:
            await other_manager.delete_user_sandbox(self.user_id)
            await other_manager.close()

    async def test_offline_runtime_and_crlf_edit(self) -> None:
        backend = await self.manager.get_backend(self.user_id, uuid4())
        backend.execute("true")
        container = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(container)
        assert container is not None
        container.reload()
        host_config = container.attrs["HostConfig"]
        self.assertTrue(host_config["ReadonlyRootfs"])
        self.assertEqual(host_config["NetworkMode"], "none")
        self.assertIn("ALL", host_config["CapDrop"])
        self.assertIn("no-new-privileges:true", host_config["SecurityOpt"])
        self.assertNotEqual(backend.execute("id -u").output.strip(), "0")
        dependency_response = backend.execute(
            "python3 -c 'import duckdb, pandas, sklearn' "
            "&& node -e \"require('lodash'); require('papaparse')\""
        )
        self.assertEqual(dependency_response.exit_code, 0)
        network_response = backend.execute(
            "python3 -c \"import urllib.request; urllib.request.urlopen('https://example.com', timeout=2)\""
        )
        self.assertNotEqual(network_response.exit_code, 0)

        isolation_response = backend.execute(
            "printf '%s\\n' \"$TMPDIR\"; umask; "
            "touch permission-check; stat -c '%a' permission-check; "
            "stat -c '%a' .home .cache .tmp"
        )
        self.assertEqual(isolation_response.exit_code, 0)
        isolation_lines = isolation_response.output.splitlines()
        self.assertTrue(isolation_lines[0].endswith("/.tmp"))
        self.assertEqual(isolation_lines[1:], ["0077", "600", "700", "700", "700"])

        self.assertIsNone(backend.write("/windows.txt", "first\r\nsecond\r\n").error)
        edit_result = backend.edit("/windows.txt", "first\nsecond", "updated\ntext")
        self.assertIsNone(edit_result.error)
        raw_file = backend.download_files(["/windows.txt"])[0]
        self.assertEqual(raw_file.content, b"updated\r\ntext\r\n")

    async def test_output_and_file_limits(self) -> None:
        backend = await self.manager.get_backend(self.user_id, uuid4())
        response = backend.execute("python3 -c \"print('x' * 7000000)\"")
        self.assertTrue(response.truncated)
        self.assertLessEqual(len(response.output.encode()), 4 * 1024 * 1024)

        offload = backend.execute_with_offload(
            "python3 -c \"print('y' * 7000000)\"",
            "/large_tool_results/result.txt",
            max_inline_bytes=100,
        )
        self.assertTrue(offload.offloaded)
        self.assertTrue(offload.response.truncated)
        self.assertIsNone(backend.read("/large_tool_results/result.txt").error)

        with self.assertRaises(SandboxFileTooLargeError):
            await self.manager.upload_user_attachment(
                self.user_id,
                uuid4(),
                "large.bin",
                io.BytesIO(b"x" * (6 * 1024 * 1024 + 1)),
            )

        quota_backend = await self.manager.get_backend(self.user_id, uuid4())
        uploads = await asyncio.gather(
            *(
                asyncio.to_thread(
                    quota_backend.upload_fileobj,
                    f"parallel-{index}.bin",
                    io.BytesIO(b"z" * (5 * 1024 * 1024)),
                )
                for index in range(5)
            )
        )
        self.assertTrue(
            any(
                response.error
                and response.error.startswith("workspace_limit_exceeded:")
                for response in uploads
            )
        )
        workspace_size = int(quota_backend.execute("du -sb . | cut -f1").output.strip())
        self.assertLessEqual(workspace_size, 24 * 1024 * 1024)

    async def test_container_spec_update_recreates_container_and_keeps_files(
        self,
    ) -> None:
        conversation_id = uuid4()
        backend = await self.manager.get_backend(self.user_id, conversation_id)
        self.assertIsNone(backend.write("/persisted.txt", "kept").error)
        old_container = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(old_container)
        assert old_container is not None
        old_container_id = old_container.id
        await self.manager.close()

        from app.clients.docker_sandbox_manager import DockerSandboxManager

        self.manager = DockerSandboxManager(build_sandbox_config(memory_limit="640m"))
        await self.manager.init()
        recreated = await self.manager.get_backend(self.user_id, conversation_id)

        recreated_container = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(recreated_container)
        assert recreated_container is not None
        self.assertNotEqual(recreated_container.id, old_container_id)
        self.assertEqual(
            recreated.download_files(["/persisted.txt"])[0].content,
            b"kept",
        )

    async def test_idle_container_restarts_on_next_operation(self) -> None:
        backend = await self.manager.get_backend(self.user_id, uuid4())
        _, user_guard, start_lock, _, _ = await self.manager._get_resources(
            self.user_id
        )
        with self.manager._activity_lock:
            self.manager._last_activity[self.user_id] = (
                time.time() - self.manager._config.idle_stop_seconds - 1
            )
        await asyncio.to_thread(
            self.manager._cleanup_idle_container_sync,
            self.user_id,
            user_guard,
            start_lock,
        )
        container = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(container)
        assert container is not None
        container.reload()
        self.assertNotEqual(container.status, "running")

        self.assertEqual(backend.execute("printf restarted").output, "restarted")
        container.reload()
        self.assertEqual(container.status, "running")

    async def test_activity_timestamp_survives_manager_restart(self) -> None:
        conversation_id = uuid4()
        await self.manager.get_backend(self.user_id, conversation_id)
        persisted_at = time.time() - 30
        with self.manager._activity_lock:
            self.manager._last_activity[self.user_id] = persisted_at
        await self.manager.close()

        from app.clients.docker_sandbox_manager import DockerSandboxManager

        self.manager = DockerSandboxManager(build_sandbox_config())
        await self.manager.init()
        recovered_at = self.manager._last_activity_timestamp(self.user_id)
        self.assertAlmostEqual(recovered_at, persisted_at, delta=1)

    async def test_idle_container_is_removed_and_cached_backend_recovers(self) -> None:
        conversation_id = uuid4()
        backend = await self.manager.get_backend(self.user_id, conversation_id)
        self.assertIsNone(backend.write("/persisted.txt", "kept").error)
        old_container = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(old_container)
        assert old_container is not None
        old_container_id = old_container.id
        _, user_guard, start_lock, _, _ = await self.manager._get_resources(
            self.user_id
        )
        with self.manager._activity_lock:
            self.manager._last_activity[self.user_id] = (
                time.time() - self.manager._config.idle_remove_seconds - 1
            )

        await asyncio.to_thread(
            self.manager._cleanup_idle_container_sync,
            self.user_id,
            user_guard,
            start_lock,
        )

        self.assertIsNone(self.manager._get_existing_container_sync(self.user_id))
        volume = self.manager._get_client().volumes.get(
            self.manager._volume_name(self.user_id)
        )
        self.assertIsNotNone(volume)
        self.assertEqual(
            backend.download_files(["/persisted.txt"])[0].content,
            b"kept",
        )
        recreated = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(recreated)
        assert recreated is not None
        self.assertNotEqual(recreated.id, old_container_id)

    async def test_idle_cleanup_never_stops_an_active_operation(self) -> None:
        backend = await self.manager.get_backend(self.user_id, uuid4())
        operation = asyncio.create_task(
            asyncio.to_thread(backend.execute, "sleep 0.6; printf completed")
        )
        _, user_guard, start_lock, _, _ = await self.manager._get_resources(
            self.user_id
        )
        for _ in range(20):
            active_container = self.manager._get_existing_container_sync(self.user_id)
            if (
                user_guard.active_operations
                and active_container is not None
                and active_container.status == "running"
            ):
                break
            await asyncio.sleep(0.05)
        with self.manager._activity_lock:
            self.manager._last_activity[self.user_id] = (
                time.time() - self.manager._config.idle_remove_seconds - 1
            )

        await asyncio.to_thread(
            self.manager._cleanup_idle_container_sync,
            self.user_id,
            user_guard,
            start_lock,
        )

        container = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(container)
        assert container is not None
        self.assertEqual(container.status, "running")
        self.assertEqual((await operation).output, "completed")

    async def test_running_container_limit_evicts_idle_lru_user(self) -> None:
        await self.manager.delete_user_sandbox(self.user_id)
        await self.manager.close()

        from app.clients.docker_sandbox_manager import DockerSandboxManager

        self.manager = DockerSandboxManager(
            build_sandbox_config(max_running_containers=1)
        )
        await self.manager.init()
        second_user_id = self.user_id + 1
        self.extra_user_ids.add(second_user_id)
        first = await self.manager.get_backend(self.user_id, uuid4())
        second = await self.manager.get_backend(second_user_id, uuid4())
        self.assertEqual(first.execute("printf first").output, "first")
        self.assertEqual(second.execute("printf second").output, "second")

        first_container = self.manager._get_existing_container_sync(self.user_id)
        second_container = self.manager._get_existing_container_sync(second_user_id)
        self.assertIsNotNone(first_container)
        self.assertIsNotNone(second_container)
        assert first_container is not None and second_container is not None
        first_container.reload()
        second_container.reload()
        self.assertNotEqual(first_container.status, "running")
        self.assertEqual(second_container.status, "running")

    async def test_running_container_limit_queues_while_all_slots_are_active(
        self,
    ) -> None:
        await self.manager.delete_user_sandbox(self.user_id)
        await self.manager.close()

        from app.clients.docker_sandbox_manager import DockerSandboxManager

        self.manager = DockerSandboxManager(
            build_sandbox_config(max_running_containers=1)
        )
        await self.manager.init()
        second_user_id = self.user_id + 1
        self.extra_user_ids.add(second_user_id)
        first = await self.manager.get_backend(self.user_id, uuid4())
        second = await self.manager.get_backend(second_user_id, uuid4())

        first_task = asyncio.create_task(
            asyncio.to_thread(first.execute, "sleep 0.6; printf first")
        )
        for _ in range(20):
            first_container = self.manager._get_existing_container_sync(self.user_id)
            if first_container is not None and first_container.status == "running":
                break
            await asyncio.sleep(0.05)
        second_task = asyncio.create_task(
            asyncio.to_thread(second.execute, "printf second")
        )
        await asyncio.sleep(0.1)
        self.assertFalse(second_task.done())

        self.assertEqual((await first_task).output, "first")
        self.assertEqual((await second_task).output, "second")

    async def test_conversation_delete_waits_for_active_operation(self) -> None:
        conversation_id = uuid4()
        backend = await self.manager.get_backend(self.user_id, conversation_id)
        operation = asyncio.create_task(
            asyncio.to_thread(backend.execute, "sleep 0.5; printf completed")
        )
        await asyncio.sleep(0.1)

        deletion = asyncio.create_task(
            self.manager.delete_conversation(self.user_id, conversation_id)
        )
        await asyncio.sleep(0.1)
        self.assertFalse(deletion.done())
        self.assertEqual((await operation).output, "completed")
        await deletion

        with self.assertRaises(SandboxDeletedError):
            backend.execute("printf should-not-run")


if __name__ == "__main__":
    unittest.main()
