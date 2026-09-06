"""Sandbox 测试使用的最小依赖替身。"""

from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

from app.shared.config.app_config import SandboxConfig


def build_sandbox_config(**updates: object) -> SandboxConfig:
    """构造沙箱管理器测试所需的最小配置。"""
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
        "max_user_storage_bytes": 24 * 1024 * 1024,
        "volume_driver": "local",
        "volume_driver_options": {},
        "idle_stop_seconds": 60,
        "idle_remove_seconds": 120,
        "cleanup_interval_seconds": 60,
        "cleanup_failure_alert_threshold": 3,
        "max_running_containers": 2,
        "stop_containers_on_shutdown": True,
    }
    values.update(updates)
    return SandboxConfig.model_validate(values)


class FakeSandboxOwnership:
    """提供无跨进程协调的 SandboxOwnership 测试替身。"""

    def __init__(self, *, last_runtime: bool = True) -> None:
        """设置释放运行时时返回的最后运行时标志。"""
        self._last_runtime = last_runtime
        self._activity: dict[int, float] = {}

    def start_runtime(self) -> None:
        """完成空运行时登记。"""

    @contextmanager
    def release_runtime(self) -> Generator[bool]:
        """返回测试指定的最后运行时标志。"""
        yield self._last_runtime

    @contextmanager
    def capacity(self) -> Generator[None]:
        """允许测试直接进入容量临界区。"""
        yield

    @contextmanager
    def user_mutation(self, user_id: int) -> Generator[None]:
        """允许测试直接执行用户结构变更。"""
        del user_id
        yield

    def assert_available(
        self,
        user_id: int,
        conversation_id: UUID | None = None,
    ) -> None:
        """允许测试访问任意未由本地 Guard 拒绝的资源。"""
        del user_id, conversation_id

    def mark_conversation_deleted(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """忽略测试中的跨进程会话墓碑。"""
        del user_id, conversation_id

    def mark_user_deleted(self, user_id: int) -> None:
        """忽略测试中的跨进程用户墓碑。"""
        del user_id

    @contextmanager
    def operation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> Generator[None]:
        """允许测试直接进入操作范围。"""
        del user_id, conversation_id
        yield

    @contextmanager
    def conversation_maintenance(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> Generator[None]:
        """允许测试直接进入会话维护范围。"""
        del user_id, conversation_id
        yield

    @contextmanager
    def user_maintenance(self, user_id: int) -> Generator[None]:
        """允许测试直接进入用户维护范围。"""
        del user_id
        yield

    def touch(self, user_id: int, activity_at: float) -> None:
        """记录测试使用的用户活动时间。"""
        self._activity[user_id] = activity_at

    def last_activity(self, user_id: int) -> float:
        """返回测试记录的用户活动时间。"""
        return self._activity.get(user_id, 0.0)

    def forget_user(self, user_id: int) -> None:
        """删除测试记录的用户活动时间。"""
        self._activity.pop(user_id, None)

    def is_user_active(self, user_id: int) -> bool:
        """测试替身不存在活动操作。"""
        del user_id
        return False

    def close(self) -> None:
        """完成空资源关闭。"""
