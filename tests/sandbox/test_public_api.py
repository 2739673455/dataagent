"""公共入口、产物路径与 Shell 生命周期。"""

import ast
import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.sandbox import DockerSandboxBackend, DockerSandboxManager
from app.sandbox.errors import SandboxPathError
from tests.sandbox.fakes import FakeSandboxOwnership


def test_production_consumers_only_import_public_sandbox_api():
    app = Path(__file__).resolve().parents[2] / "app"
    for path in app.rglob("*.py"):
        if path.is_relative_to(app / "sandbox"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.sandbox."), path
            elif isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith("app.sandbox.") for alias in node.names
                ), path


def test_artifact_write_returns_canonical_path_and_rejects_escape():
    manager = DockerSandboxManager(ownership=FakeSandboxOwnership())
    conversation = uuid4()
    content = io.BytesIO(b"test")

    async def run():
        with (
            patch.object(manager, "init", new_callable=AsyncMock) as initialize,
            patch.object(manager, "_upload_attachment_sync") as upload,
        ):
            path = await manager.write_artifact(
                1, conversation, "reports//data.csv", content
            )
            assert path == f"/data/{conversation}/reports/data.csv"
            upload.assert_called_once_with(1, conversation, "reports/data.csv", content)
            initialize.reset_mock()
            with pytest.raises(SandboxPathError):
                await manager.write_artifact(1, conversation, "../other.csv", content)
            initialize.assert_not_awaited()

    asyncio.run(run())


def test_shell_cancellation_cleans_up_before_propagating():
    async def run():
        backend = DockerSandboxBackend(
            1, uuid4(), 10001, FakeSandboxOwnership(), lambda: None, MagicMock()
        )
        started = asyncio.Event()

        async def execute(job_id, command):
            started.set()
            await asyncio.Event().wait()

        runner = MagicMock(arun=AsyncMock(side_effect=execute), acleanup=AsyncMock())
        backend._shell_jobs = runner
        task = asyncio.create_task(backend.run_shell("sleep 100"))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        runner.acleanup.assert_awaited_once_with(
            runner.arun.call_args.args[0], remove_log=True
        )

    asyncio.run(run())


def test_artifact_resolution_validates_scope_and_downloadability():
    manager = DockerSandboxManager(ownership=FakeSandboxOwnership())
    conversation = uuid4()
    container = MagicMock()

    async def run():
        with (
            patch.object(manager, "init", new_callable=AsyncMock) as initialize,
            patch.object(
                manager, "_get_existing_container_sync", return_value=container
            ),
            patch.object(
                manager._archive, "is_downloadable_file", return_value=True
            ) as inspect,
        ):
            for path in (
                "report.csv",
                f"/data/{uuid4()}/report.csv",
                f"/data/{conversation}/.private/report.csv",
                f"/data/{conversation}/../report.csv",
            ):
                assert await manager.resolve_artifact(1, conversation, path) is None
            initialize.assert_not_awaited()
            inspect.assert_not_called()
            assert (
                await manager.resolve_artifact(
                    1, conversation, f"/data/{conversation}/reports//data.csv"
                )
                == "reports/data.csv"
            )
            inspect.assert_called_once_with(container, conversation, "reports/data.csv")
            inspect.return_value = False
            assert (
                await manager.resolve_artifact(
                    1, conversation, f"/data/{conversation}/missing.csv"
                )
                is None
            )
            inspect.side_effect = OSError("Docker unavailable")
            with pytest.raises(OSError, match="Docker unavailable"):
                await manager.resolve_artifact(
                    1, conversation, f"/data/{conversation}/report.csv"
                )

    asyncio.run(run())
