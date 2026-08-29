import asyncio
import io
import os
import threading
import time
import unittest
from collections.abc import Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from docker.errors import ImageNotFound
from pydantic import ValidationError

from app.analytics.agents.manager import AgentManager
from app.analytics.agents.shell_jobs import ShellJobResult, ShellJobRuntime
from app.analytics.agents.skills import packaged_agent_skill_mounts
from app.sandbox.archive import SandboxArchiveStore
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.capacity import FairCapacityLimiter
from app.sandbox.concurrency import LifecycleGuard
from app.sandbox.exceptions import (
    SandboxCapacityCancelledError,
    SandboxCapacityQueueFullError,
    SandboxCapacityTimeoutError,
    SandboxDeletedError,
    SandboxFileTooLargeError,
    SandboxPathError,
)
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.ownership import LocalSandboxOwnership
from app.sandbox.paths import (
    SandboxSessionScope,
    normalize_attachment_path,
    normalize_user_attachment_path,
)
from app.shared.config.app_config import SandboxConfig


def build_sandbox_config(**updates: object) -> SandboxConfig:
    values = {
        "deployment_namespace": "test",
        "ownership": {
            "redis_url": "redis://127.0.0.1:6379/15",
            "lock_timeout_seconds": 60,
            "wait_timeout_seconds": 2,
            "lease_seconds": 10,
        },
        "image": "dataagent-sandbox:latest",
        "memory_limit": "512m",
        "nano_cpus": 1_000_000_000,
        "pids_limit": 64,
        "network_mode": "none",
        "internal_command_timeout_seconds": 60,
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


def build_sandbox_manager(config: SandboxConfig) -> DockerSandboxManager:
    """构造使用进程内协调器的测试沙箱管理器"""
    return DockerSandboxManager(
        config,
        LocalSandboxOwnership(),
        packaged_agent_skill_mounts(),
    )


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

    def test_user_attachment_adds_one_uploads_prefix(self) -> None:
        self.assertEqual(
            normalize_user_attachment_path("report.csv"),
            "uploads/report.csv",
        )
        self.assertEqual(
            normalize_user_attachment_path("reports/summary.csv"),
            "uploads/reports/summary.csv",
        )
        self.assertEqual(
            normalize_user_attachment_path("uploads/report.csv"),
            "uploads/report.csv",
        )

    def test_system_paths_and_nested_analysis_names_remain_valid(self) -> None:
        self.assertEqual(
            normalize_attachment_path("analyses/run/report.csv"),
            "analyses/run/report.csv",
        )
        self.assertEqual(
            normalize_user_attachment_path("analyses/report.csv"),
            "uploads/analyses/report.csv",
        )


class AttachmentCapabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_system_writer_allows_analysis_path(self) -> None:
        manager = build_sandbox_manager(build_sandbox_config())
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

    async def test_user_mutations_reject_unsafe_path_before_io(self) -> None:
        manager = build_sandbox_manager(build_sandbox_config())
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
                    "../report.csv",
                    io.BytesIO(b"overwrite"),
                )
            with self.assertRaises(SandboxPathError):
                await manager.delete_user_attachment(
                    7,
                    conversation_id,
                    "../report.csv",
                )

        writer.assert_not_awaited()
        get_backend.assert_not_awaited()


class SandboxConfigTest(unittest.TestCase):
    def test_accepts_ordered_size_limits(self) -> None:
        config = build_sandbox_config()
        self.assertEqual(config.network_mode, "none")

    def test_rejects_file_limit_larger_than_workspace(self) -> None:
        with self.assertRaises(ValidationError):
            build_sandbox_config(max_file_bytes=24 * 1024 * 1024 + 1)

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


class DockerSandboxInitializationTest(unittest.TestCase):
    def test_loads_existing_image_without_building(self) -> None:
        manager = build_sandbox_manager(build_sandbox_config())
        client = MagicMock()
        client.images.get.return_value.id = "sha256:test-image"

        with patch("app.sandbox.manager.docker.from_env", return_value=client):
            manager._init_sync()

        client.ping.assert_called_once_with()
        client.images.get.assert_called_once_with("dataagent-sandbox:latest")
        client.images.build.assert_not_called()
        self.assertIs(manager._client, client)

    def test_missing_image_reports_explicit_build_command(self) -> None:
        manager = build_sandbox_manager(build_sandbox_config())
        client = MagicMock()
        client.images.get.side_effect = ImageNotFound("missing image")

        with (
            patch("app.sandbox.manager.docker.from_env", return_value=client),
            self.assertRaisesRegex(RuntimeError, "docker compose"),
        ):
            manager._init_sync()

        client.close.assert_called_once_with()


class FairCapacityLimiterTest(unittest.TestCase):
    def test_queue_is_bounded_and_wait_can_be_cancelled(self) -> None:
        limiter = FairCapacityLimiter(1, 1, 1)
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
        limiter = FairCapacityLimiter(1, 4, 2)
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
        limiter = FairCapacityLimiter(1, 1, 0.05)
        self.assertTrue(limiter.acquire(1, lambda _: 0, lambda _: False))
        limiter.complete_reservation(1, running=True)
        with self.assertRaises(SandboxCapacityTimeoutError):
            limiter.acquire(2, lambda _: 0, lambda _: False)


class LifecycleGuardTest(unittest.TestCase):
    def test_maintenance_waits_for_active_operation(self) -> None:
        guard = LifecycleGuard()
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
        guard = LifecycleGuard()
        with guard.maintenance():
            guard.mark_deleted()
        with self.assertRaises(SandboxDeletedError), guard.operation():
            pass

    def test_deleted_guard_allows_only_explicit_cleanup_maintenance(self) -> None:
        guard = LifecycleGuard()
        guard.mark_deleted()

        with self.assertRaises(SandboxDeletedError), guard.maintenance():
            pass
        with guard.maintenance(allow_deleted=True):
            pass


class DockerSandboxManagerPolicyTest(unittest.TestCase):
    @staticmethod
    def _session_backend(
        config: SandboxConfig,
        conversation_id: UUID,
    ) -> DockerSandboxBackend:
        scope = SandboxSessionScope(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )
        conversation_guard = LifecycleGuard()
        return DockerSandboxBackend(
            user_id=7,
            conversation_id=conversation_id,
            conversation_uid=100_001,
            sandbox_config=config,
            ownership=LocalSandboxOwnership(),
            user_guard=LifecycleGuard(),
            conversation_guard=conversation_guard,
            mutation_lock=threading.RLock(),
            touch=lambda: None,
            get_running_container=lambda _: MagicMock(),
            notify_capacity_waiters=lambda: None,
            session_scope=scope,
            execution_uid=100_002,
        )

    def test_session_backend_maps_reads_and_scopes_mutations(self) -> None:
        conversation_id = uuid4()
        backend = self._session_backend(build_sandbox_config(), conversation_id)
        own_virtual_path = "/analyses/sales-decline/sessions/analyst/region/result.json"
        sibling_virtual_path = (
            "/analyses/sales-decline/sessions/explorer/base/dataset.csv"
        )
        conversation_root = f"/workspace/conversations/{conversation_id}"

        self.assertEqual(
            backend._resolve_path("result.json"),
            f"{conversation_root}{own_virtual_path}",
        )
        self.assertEqual(
            backend._resolve_path(sibling_virtual_path),
            f"{conversation_root}{sibling_virtual_path}",
        )
        self.assertEqual(
            backend._resolve_mutation_path(own_virtual_path),
            f"{conversation_root}{own_virtual_path}",
        )
        with self.assertRaises(SandboxPathError):
            backend._resolve_mutation_path(sibling_virtual_path)
        with self.assertRaises(SandboxPathError):
            backend._resolve_mutation_path("/uploads/input.csv")

        other_conversation_path = (
            f"/workspace/conversations/{uuid4()}/analyses/private.json"
        )
        self.assertTrue(
            backend._resolve_path(other_conversation_path).startswith(
                f"{conversation_root}/"
            )
        )

    def test_archive_delete_session_removes_workspace_staging_and_uid(self) -> None:
        conversation_id = uuid4()
        scope = SandboxSessionScope("sales-decline", "analyst", "region")
        registry_key = scope.registry_key(conversation_id)
        registry = SimpleNamespace(
            conversations={str(conversation_id): 100_001},
            sessions={registry_key: 100_002},
        )
        store = SandboxArchiveStore(1024, 4096)
        container = MagicMock()
        container.exec_run.return_value = SimpleNamespace(exit_code=0, output=b"")

        with (
            patch.object(store, "_load_registry", return_value=registry),
            patch.object(store, "inspect_path", return_value=MagicMock()),
            patch.object(store, "_write_registry") as write_registry,
        ):
            deleted = store.delete_session(container, conversation_id, scope)

        self.assertTrue(deleted)
        command = container.exec_run.call_args.args[0]
        self.assertEqual(command[:3], ["rm", "-rf", "--"])
        self.assertIn(scope.relative_workspace, command[3])
        self.assertTrue(command[4].endswith("/100002"))
        self.assertNotIn(registry_key, registry.sessions)
        write_registry.assert_called_once_with(container, registry)

    def test_internal_execute_timeout_is_clamped_to_sandbox_limit(self) -> None:
        backend = self._session_backend(
            build_sandbox_config(internal_command_timeout_seconds=7),
            uuid4(),
        )
        api_client = MagicMock()
        api_client.exec_create.return_value = {"Id": "exec-id"}
        api_client.exec_start.return_value = iter(())
        api_client.exec_inspect.return_value = {"ExitCode": 0}
        container = MagicMock()
        container.id = "container-id"
        container.client.api = api_client
        backend._operation_local.container = container

        backend._execute_unlocked("printf done", timeout=999)

        shell_command = api_client.exec_create.call_args.args[1]
        self.assertEqual(shell_command[:3], ["timeout", "--signal=KILL", "7"])

    def test_shell_job_uses_unbounded_wrapper_and_reads_final_control(self) -> None:
        backend = self._session_backend(build_sandbox_config(), uuid4())
        api_client = MagicMock()
        api_client.exec_create.return_value = {"Id": "shell-job-exec"}
        api_client.exec_start.return_value = iter(())
        api_client.exec_inspect.return_value = {"ExitCode": 0}
        container = MagicMock()
        container.id = "container-id"
        container.client.api = api_client
        backend._operation_local.container = container

        with (
            patch.object(
                backend,
                "_workspace_size_unlocked",
                side_effect=[0, 0],
            ),
            patch.object(
                backend,
                "_read_shell_job_control_unlocked",
                return_value={
                    "status": "finished",
                    "exit_code": 0,
                    "output_truncated": False,
                },
            ),
            patch.object(
                backend,
                "_read_limited_file_bytes_unlocked",
                return_value=(b"done\n", 0),
            ),
        ):
            result = backend.run_shell_job("job_1234abcd", "printf done")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output, "done\n")
        create_call = api_client.exec_create.call_args
        shell_command = create_call.args[1]
        self.assertEqual(shell_command[:2], ["python3", "-c"])
        self.assertNotIn("timeout", shell_command)
        self.assertEqual(create_call.kwargs["user"], "0")
        self.assertTrue(create_call.kwargs["privileged"])

    def test_cancel_shell_job_runs_controlled_process_group_terminator(self) -> None:
        backend = self._session_backend(build_sandbox_config(), uuid4())
        container = MagicMock()
        container.exec_run.return_value = SimpleNamespace(
            exit_code=0,
            output=b'{"ready":true,"signal_sent":true,"exited":true}\n',
        )
        backend._operation_local.container = container

        result = backend.cancel_shell_job("job_1234abcd")

        self.assertTrue(result.ready)
        self.assertTrue(result.signal_sent)
        self.assertTrue(result.exited)
        command = container.exec_run.call_args.args[0]
        self.assertIn("python3", command)
        self.assertTrue(command[-2].endswith("/shell_jobs/job_1234abcd.json"))

    def test_resource_names_and_volume_options_are_namespaced(self) -> None:

        manager = build_sandbox_manager(
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

        manager = build_sandbox_manager(build_sandbox_config())
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

    def test_packaged_agent_skills_use_read_only_container_mounts(self) -> None:
        manager = build_sandbox_manager(build_sandbox_config())

        volumes = manager._readonly_mount_volumes()

        self.assertEqual(len(volumes), 1)
        self.assertEqual(
            next(iter(volumes.values())),
            {"bind": "/skills/analyst", "mode": "ro"},
        )

    def test_health_exposes_cleanup_and_capacity_state(self) -> None:

        manager = build_sandbox_manager(build_sandbox_config())
        manager._record_cleanup_result(time.time(), ["docker unavailable"])
        health = manager.health()
        self.assertEqual(health.cleanup_consecutive_failures, 1)
        self.assertEqual(health.cleanup_last_error, "docker unavailable")
        self.assertEqual(health.capacity.max_waiting, 16)
        self.assertEqual(health.quota_mode, "application")


class DockerSandboxCleanupHealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_failure_is_recorded_and_next_cycle_recovers(self) -> None:

        manager = build_sandbox_manager(build_sandbox_config())

        async def run_inline(
            function: Callable[..., object],
            *args: object,
            **kwargs: object,
        ) -> object:
            return function(*args, **kwargs)

        with patch(
            "app.sandbox.manager.asyncio.to_thread",
            side_effect=run_inline,
        ):
            with (
                patch.object(
                    manager,
                    "_managed_user_ids_sync",
                    side_effect=RuntimeError("docker unavailable"),
                ),
                patch("app.sandbox.manager.logger.exception"),
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
        tombstones = MagicMock()
        tombstones.save = AsyncMock()
        tombstones.exists = AsyncMock(return_value=False)
        tombstones.delete_by_user = AsyncMock()

        @asynccontextmanager
        async def advisory_lock(*args: object, **kwargs: object):
            del args, kwargs
            yield

        persistence_manager.advisory_lock = advisory_lock
        manager = AgentManager(persistence_manager, MagicMock(), tombstones)
        runtime = MagicMock()
        runtime.planner_lock = advisory_lock
        runtime.session_service.planner_run = advisory_lock
        runtime.conversation_deleted = AsyncMock(return_value=False)
        user_id = 7
        conversation_id = uuid4()
        started = asyncio.Event()

        async def run() -> None:
            async with manager.execution(
                user_id,
                conversation_id,
                runtime=runtime,
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

        config = build_sandbox_config()
        client = docker.from_env()
        try:
            try:
                client.images.get(config.image)
            except ImageNotFound as exc:
                raise RuntimeError(
                    f"Docker 沙箱镜像不存在: {config.image}，"
                    "请先执行 docker compose -f docker/compose.yml up -d"
                ) from exc
        finally:
            client.close()

    async def asyncSetUp(self) -> None:

        self.user_id = 2_000_000_000 + os.getpid()
        self.extra_user_ids: set[int] = set()
        self.manager = build_sandbox_manager(build_sandbox_config())
        await self.manager.init()

    async def asyncTearDown(self) -> None:
        for user_id in {self.user_id, *self.extra_user_ids}:
            await self.manager.delete_user_sandbox(user_id)
        await self.manager.close()

    def _set_last_activity(self, activity_at: float) -> None:
        with self.manager._activity_lock:
            self.manager._last_activity[self.user_id] = activity_at
        self.manager._ownership.touch(self.user_id, activity_at)

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

    async def test_packaged_agent_skills_are_readable_and_immutable(self) -> None:
        backend = await self.manager.get_session_backend(
            self.user_id,
            uuid4(),
            "analysis-run",
            "analyst",
            "analyst-session",
        )
        skill_path = "/skills/analyst/analysis/SKILL.md"

        result = backend.execute(
            f"grep -q '^name: analysis$' {skill_path} "
            f"&& ! printf changed >> {skill_path}"
        )

        self.assertEqual(result.exit_code, 0, result.output)

    async def test_user_mutations_are_scoped_away_from_analysis_artifact(self) -> None:
        conversation_id = uuid4()
        artifact_path = "analyses/run/report.csv"
        await self.manager.write_artifact(
            self.user_id,
            conversation_id,
            artifact_path,
            io.BytesIO(b"verified artifact"),
        )

        uploaded_path = await self.manager.upload_user_attachment(
            self.user_id,
            conversation_id,
            artifact_path,
            io.BytesIO(b"user upload"),
        )
        self.assertEqual(uploaded_path, "uploads/analyses/run/report.csv")
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
        self.assertTrue(first_identity.endswith(" 750"))
        self.assertTrue(second_identity.endswith(" 750"))
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
        self.assertEqual(
            first.execute(f"ln -s ../{second_id} uploads").exit_code,
            0,
        )
        with self.assertRaises(SandboxPathError):
            await self.manager.upload_user_attachment(
                self.user_id,
                first_id,
                "http-injected.txt",
                io.BytesIO(b"HTTP_INJECTED_CONTENT"),
            )
        with self.assertRaises(SandboxPathError):
            await self.manager.download_file(
                self.user_id,
                first_id,
                "linked_directory/data.txt",
            )

    async def test_sessions_share_reads_and_reject_sibling_mutations(self) -> None:
        conversation_id = uuid4()
        explorer = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "explorer",
            "base",
        )
        analyst = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "analyst",
            "region",
        )
        artifact_path = "/analyses/sales-decline/sessions/explorer/base/dataset.csv"

        write_result = explorer.write("dataset.csv", "region,sales\neast,42\n")
        self.assertIsNone(write_result.error)
        self.assertEqual(write_result.path, artifact_path)
        self.assertTrue(
            await self.manager.is_file(
                self.user_id,
                conversation_id,
                artifact_path.lstrip("/"),
            )
        )
        self.assertEqual(
            await self.manager.download_file(
                self.user_id,
                conversation_id,
                artifact_path.lstrip("/"),
            ),
            b"region,sales\neast,42\n",
        )
        read_result = analyst.read(artifact_path)
        self.assertIsNone(read_result.error)
        self.assertIsNotNone(read_result.file_data)
        assert read_result.file_data is not None
        self.assertIn("east,42", read_result.file_data["content"])
        shell_artifact_path = (
            '"$DATAAGENT_CONVERSATION_ROOT/'
            'analyses/sales-decline/sessions/explorer/base/dataset.csv"'
        )
        shell_read = analyst.execute(f"cat {shell_artifact_path}")
        self.assertEqual(shell_read.exit_code, 0)
        self.assertIn("east,42", shell_read.output)

        self.assertIsNotNone(analyst.write(artifact_path, "tampered").error)
        self.assertIsNotNone(analyst.edit(artifact_path, "east", "west").error)
        self.assertIsNotNone(analyst.delete(artifact_path).error)

        overwrite = analyst.execute(f"printf tampered > {shell_artifact_path}")
        removal = analyst.execute(f"rm -f {shell_artifact_path}")
        self.assertNotEqual(overwrite.exit_code, 0)
        self.assertNotEqual(removal.exit_code, 0)
        preserved = explorer.read("dataset.csv")
        self.assertIsNone(preserved.error)
        self.assertIsNotNone(preserved.file_data)
        assert preserved.file_data is not None
        self.assertIn("east,42", preserved.file_data["content"])

    async def test_delete_session_removes_files_and_allows_clean_recreation(
        self,
    ) -> None:
        conversation_id = uuid4()
        backend = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "analyst",
            "region",
        )
        self.assertIsNone(backend.write("old.txt", "old state").error)

        deleted = await self.manager.delete_session(
            self.user_id,
            conversation_id,
            "sales-decline",
            "analyst",
            "region",
        )
        recreated = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "analyst",
            "region",
        )

        self.assertTrue(deleted)
        self.assertIsNotNone(recreated.read("old.txt").error)
        self.assertIsNone(recreated.write("new.txt", "new state").error)

    async def test_session_execute_reports_virtual_working_directory(self) -> None:
        conversation_id = uuid4()
        backend = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "reviewer",
            "sales-trend",
        )

        response = backend.execute("pwd")

        self.assertEqual(response.exit_code, 0)
        self.assertEqual(
            response.output.strip(),
            "/analyses/sales-decline/sessions/reviewer/sales-trend",
        )
        self.assertNotIn("/workspace/conversations", response.output)

    async def test_shell_job_continues_after_foreground_wait_and_merges_output(
        self,
    ) -> None:
        conversation_id = uuid4()
        backend = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "analyst",
            "background",
        )
        runtime = ShellJobRuntime(backend, foreground_wait_seconds=0.05)

        running = await runtime.execute(
            "printf 'stdout\\n'; printf 'stderr\\n' >&2; sleep 0.2; printf 'done\\n'"
        )
        final = await runtime.get(running.job_id, wait_seconds=2)
        self.assertIsInstance(final, ShellJobResult)
        assert isinstance(final, ShellJobResult)
        log = backend.read(final.output_path)
        await runtime.cleanup()

        self.assertEqual(running.status, "running")
        self.assertEqual(final.status, "completed")
        self.assertEqual(final.exit_code, 0)
        self.assertIsNone(log.error)
        self.assertIsNotNone(log.file_data)
        assert log.file_data is not None
        log_content = log.file_data["content"]
        self.assertIn("stdout", log_content)
        self.assertIn("stderr", log_content)
        self.assertIn("done", log_content)

    async def test_shell_job_cancel_terminates_child_process_group(self) -> None:
        conversation_id = uuid4()
        backend = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "reviewer",
            "cancel",
        )
        runtime = ShellJobRuntime(backend, foreground_wait_seconds=0.05)

        running = await runtime.execute(
            "sleep 300 & child=$!; printf '%s' \"$child\" > child.pid; wait"
        )
        cancelled = await runtime.cancel(running.job_id)
        child_check = backend.execute(
            "child=$(cat child.pid); "
            "if [ -r \"/proc/$child/stat\" ]; then "
            "state=$(cut -d ' ' -f 3 \"/proc/$child/stat\"); "
            "[ \"$state\" = Z ] || exit 1; fi"
        )
        await runtime.cleanup()

        self.assertEqual(running.status, "running")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(child_check.exit_code, 0)

    async def test_session_cannot_read_another_conversation(self) -> None:
        first_conversation_id = uuid4()
        second_conversation_id = uuid4()
        first = await self.manager.get_session_backend(
            self.user_id,
            first_conversation_id,
            "sales-decline",
            "explorer",
            "base",
        )
        second = await self.manager.get_session_backend(
            self.user_id,
            second_conversation_id,
            "sales-decline",
            "analyst",
            "region",
        )
        self.assertIsNone(first.write("secret.txt", "CONVERSATION_SECRET").error)

        virtual_read = second.read(
            "/analyses/sales-decline/sessions/explorer/base/secret.txt"
        )
        direct_read = second.execute(f"cat {first.workspace_dir}/secret.txt")

        self.assertIsNotNone(virtual_read.error)
        self.assertNotEqual(direct_read.exit_code, 0)
        self.assertNotIn("CONVERSATION_SECRET", direct_read.output)

    async def test_platform_query_artifact_is_readable_by_sessions(self) -> None:
        conversation_id = uuid4()
        explorer = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "explorer",
            "base",
        )
        analyst = await self.manager.get_session_backend(
            self.user_id,
            conversation_id,
            "sales-decline",
            "analyst",
            "region",
        )
        relative_path = "analyses/sales-decline/sessions/explorer/base/query_result.csv"
        await self.manager.write_artifact(
            self.user_id,
            conversation_id,
            relative_path,
            io.BytesIO(b"region,sales\neast,42\n"),
        )

        for backend in (explorer, analyst):
            with self.subTest(backend=backend.id):
                result = backend.read(f"/{relative_path}")
                self.assertIsNone(result.error)
                self.assertIsNotNone(result.file_data)
                assert result.file_data is not None
                self.assertIn("east,42", result.file_data["content"])

    async def test_deployment_namespaces_do_not_share_resources(self) -> None:

        conversation_id = uuid4()
        first = await self.manager.get_backend(self.user_id, conversation_id)
        self.assertIsNone(first.write("/private.txt", "first-deployment").error)

        other_manager = build_sandbox_manager(
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

    async def test_direct_output_and_file_limits(self) -> None:
        backend = await self.manager.get_backend(self.user_id, uuid4())
        response = backend.execute("python3 -c \"print('x' * 7000000)\"")
        self.assertFalse(response.truncated)
        self.assertEqual(len(response.output.encode()), 7_000_001)

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

        self.manager = build_sandbox_manager(build_sandbox_config(memory_limit="640m"))
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
        resources = self.manager._get_user_resources(self.user_id)
        self._set_last_activity(
            time.time() - self.manager._config.idle_stop_seconds - 1
        )
        await asyncio.to_thread(
            self.manager._cleanup_idle_container_sync,
            self.user_id,
            resources.guard,
            resources.start_lock,
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
        self._set_last_activity(persisted_at)
        await self.manager.close()

        self.manager = build_sandbox_manager(build_sandbox_config())
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
        resources = self.manager._get_user_resources(self.user_id)
        self._set_last_activity(
            time.time() - self.manager._config.idle_remove_seconds - 1
        )

        await asyncio.to_thread(
            self.manager._cleanup_idle_container_sync,
            self.user_id,
            resources.guard,
            resources.start_lock,
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
        resources = self.manager._get_user_resources(self.user_id)
        for _ in range(20):
            active_container = self.manager._get_existing_container_sync(self.user_id)
            if (
                resources.guard.active_operations
                and active_container is not None
                and active_container.status == "running"
            ):
                break
            await asyncio.sleep(0.05)
        self._set_last_activity(
            time.time() - self.manager._config.idle_remove_seconds - 1
        )

        await asyncio.to_thread(
            self.manager._cleanup_idle_container_sync,
            self.user_id,
            resources.guard,
            resources.start_lock,
        )

        container = self.manager._get_existing_container_sync(self.user_id)
        self.assertIsNotNone(container)
        assert container is not None
        self.assertEqual(container.status, "running")
        self.assertEqual((await operation).output, "completed")

    async def test_running_container_limit_evicts_idle_lru_user(self) -> None:
        await self.manager.delete_user_sandbox(self.user_id)
        await self.manager.close()

        self.manager = build_sandbox_manager(
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

        self.manager = build_sandbox_manager(
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
