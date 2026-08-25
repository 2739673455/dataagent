"""沙箱资源组装入口"""

from app.sandbox.manager import DockerSandboxManager
from app.sandbox.ownership import RedisSandboxOwnership
from app.shared.config.app_config import SandboxConfig


def create_sandbox_manager(
    config: SandboxConfig,
) -> DockerSandboxManager:
    """按调用进程创建带 Redis 协调的沙箱管理器"""
    ownership_config = config.ownership
    ownership = RedisSandboxOwnership(
        ownership_config.redis_url,
        config.deployment_namespace,
        lock_timeout_seconds=ownership_config.lock_timeout_seconds,
        wait_timeout_seconds=ownership_config.wait_timeout_seconds,
        lease_seconds=ownership_config.lease_seconds,
    )
    return DockerSandboxManager(config, ownership)
