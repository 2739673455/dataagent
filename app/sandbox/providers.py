"""沙箱资源组装入口。"""

from collections.abc import Sequence

from app.sandbox.manager import DockerSandboxManager
from app.sandbox.ownership import RedisSandboxOwnership
from app.sandbox.paths import SandboxReadonlyMount


def create_sandbox_manager(
    readonly_mounts: Sequence[SandboxReadonlyMount],
) -> DockerSandboxManager:
    """按调用进程创建带 Redis 协调的沙箱管理器。"""
    return DockerSandboxManager(RedisSandboxOwnership(), readonly_mounts)
