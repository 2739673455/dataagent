"""测量停止态沙箱附件上传的 Archive 配额扫描成本。"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shlex
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any, cast
from uuid import uuid4

from app.sandbox.manager import DockerSandboxManager
from app.sandbox.ownership import LocalSandboxOwnership
from app.shared.config.app_config import cfg

_BENCHMARK_WORKSPACE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """记录单个附件配额基准场景的结果。"""

    scenario: str
    file_count: int
    workspace_bytes: int
    archive_bytes: int
    quota_scan_seconds: float
    upload_seconds: float


class _CountingContainer:
    """统计 Docker Archive API 返回的压缩包字节数。"""

    def __init__(self, container: Any) -> None:
        """绑定实际 Docker 容器。"""
        self._container = container
        self.archive_bytes = 0

    def get_archive(self, path: str) -> tuple[Iterator[bytes], dict[str, Any]]:
        """代理 Archive 读取并累计传输字节。"""
        chunks, metadata = self._container.get_archive(path)

        def count() -> Iterator[bytes]:
            """逐块统计并转发 Archive 内容。"""
            for chunk in chunks:
                self.archive_bytes += len(chunk)
                yield chunk

        return count(), metadata


def _benchmark_manager() -> DockerSandboxManager:
    """构造使用独立 Docker 命名空间的基准管理器。"""
    sandbox_config = cfg.sandbox.model_copy(
        update={
            "deployment_namespace": f"sandbox-bench-{os.getpid()}",
            "max_file_bytes": 16 * 1024 * 1024,
            "max_workspace_bytes": _BENCHMARK_WORKSPACE_BYTES,
            "max_running_containers": 1,
            "stop_containers_on_shutdown": True,
        }
    )
    return DockerSandboxManager(sandbox_config, LocalSandboxOwnership(), ())


def _populate_small_files(backend: Any, file_count: int) -> None:
    """在当前 Conversation 中生成指定数量的小文件。"""
    code = (
        "from pathlib import Path\n"
        "root = Path('benchmark-small-files')\n"
        "root.mkdir()\n"
        f"for index in range({file_count}):\n"
        "    (root / f'{index}.txt').write_bytes(b'x')\n"
    )
    response = backend.execute(f"python3 -c {shlex.quote(code)}")
    if response.exit_code != 0:
        raise RuntimeError(response.output)


def _populate_near_limit_files(backend: Any) -> None:
    """创建接近基准工作区容量限制的大文件。"""
    target_bytes = (_BENCHMARK_WORKSPACE_BYTES - 2 * 1024 * 1024) // 2
    response = backend.execute(
        f"truncate -s {target_bytes} benchmark-large-a.bin benchmark-large-b.bin"
    )
    if response.exit_code != 0:
        raise RuntimeError(response.output)


def _stop_container(manager: DockerSandboxManager, user_id: int) -> Any:
    """停止基准容器并释放本地运行容量。"""
    container = manager._get_existing_container_sync(user_id)
    if container is None:
        raise RuntimeError("基准容器不存在")
    container.reload()
    if container.status == "running":
        container.stop(timeout=10)
        manager._mark_user_not_running(user_id)
    container.reload()
    if container.status == "running":
        raise RuntimeError("基准容器未停止")
    return container


async def _run_scenario(
    manager: DockerSandboxManager,
    user_id: int,
    scenario: str,
    file_count: int,
    *,
    near_limit: bool = False,
) -> BenchmarkResult:
    """运行单个停止态附件上传基准场景。"""
    conversation_id = uuid4()
    backend = await manager.get_backend(user_id, conversation_id)
    try:
        if near_limit:
            await asyncio.to_thread(_populate_near_limit_files, backend)
        else:
            await asyncio.to_thread(_populate_small_files, backend, file_count)

        container = await asyncio.to_thread(_stop_container, manager, user_id)
        conversation_uid = manager._archive.ensure_workspace(
            container,
            conversation_id,
        )
        counting_container = _CountingContainer(container)
        scan_started = time.perf_counter()
        workspace_bytes = await asyncio.to_thread(
            manager._archive._workspace_size,
            cast(Any, counting_container),
            conversation_id,
            conversation_uid,
        )
        quota_scan_seconds = time.perf_counter() - scan_started

        upload_started = time.perf_counter()
        await manager.upload_user_attachment(
            user_id,
            conversation_id,
            "benchmark-upload.txt",
            io.BytesIO(b"benchmark"),
        )
        upload_seconds = time.perf_counter() - upload_started
        container.reload()
        if container.status == "running":
            raise RuntimeError("附件上传启动了基准容器")
        return BenchmarkResult(
            scenario=scenario,
            file_count=file_count,
            workspace_bytes=workspace_bytes,
            archive_bytes=counting_container.archive_bytes,
            quota_scan_seconds=quota_scan_seconds,
            upload_seconds=upload_seconds,
        )
    finally:
        await manager.delete_conversation(user_id, conversation_id)


async def _run() -> list[BenchmarkResult]:
    """依次运行文档要求的三个基准场景。"""
    manager = _benchmark_manager()
    user_id = 2_100_000_000 + os.getpid()
    await manager.init(start_cleanup=False)
    try:
        return [
            await _run_scenario(manager, user_id, "small-files-1000", 1_000),
            await _run_scenario(manager, user_id, "small-files-10000", 10_000),
            await _run_scenario(
                manager,
                user_id,
                "near-workspace-limit",
                2,
                near_limit=True,
            ),
        ]
    finally:
        await manager.delete_user_sandbox(user_id)
        await manager.close()


def main() -> int:
    """运行基准并以 JSON 输出测量结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps([asdict(result) for result in asyncio.run(_run())], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
