"""当前进程中专业 Session 的活动登记与开始时间。"""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID

from app.assistant.models.session import AgentSessionKey


class SessionActivity:
    """线程安全的进程内 Session 活动注册表。"""

    def __init__(self) -> None:
        """初始化活动开始时间映射及其访问锁。"""
        self._active: dict[AgentSessionKey, datetime] = {}
        self._lock = Lock()

    def is_active(self, key: AgentSessionKey) -> bool:
        """判断指定 Session 是否在当前进程中执行。"""
        with self._lock:
            return key in self._active

    def snapshot(
        self, user_id: int, conversation_id: UUID
    ) -> dict[AgentSessionKey, datetime]:
        """复制指定用户会话中的活动 Session 及其开始时间。"""
        with self._lock:
            return {
                key: at
                for key, at in self._active.items()
                if key.user_id == user_id and key.conversation_id == conversation_id
            }

    @contextmanager
    def track(self, key: AgentSessionKey) -> Generator[None]:
        """进入执行范围时登记活动，退出时清除登记。"""
        with self._lock:
            self._active[key] = datetime.now(UTC)
        try:
            yield
        finally:
            with self._lock:
                self._active.pop(key, None)
