"""Session 执行与删除共用的互斥和容量控制。"""

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from uuid import UUID

from app.assistant.models.session import AgentSessionKey
from app.assistant.repositories.session import SessionCheckpointRepository
from app.assistant.sessions.identity import (
    get_thread_id,
    session_checkpoint_namespace,
)
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
from app.shared.errors.infrastructure import AdvisoryLockBusyError


class SessionControl:
    """运行与删除共享 Session 锁；新上下文占用跨进程容量槽位。"""

    def __init__(
        self,
        repository: SessionCheckpointRepository,
        locks: PostgresAdvisoryLocks,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """绑定会话 Checkpoint 仓储和跨进程锁服务。"""
        self._repository = repository
        self._locks = locks
        self._thread_id = get_thread_id(user_id, conversation_id)

    def lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        """获取专业 Session 的跨进程互斥锁。"""
        return self._locks.advisory_lock(
            f"specialist:{self._thread_id}:{session_checkpoint_namespace(session_key)}",
        )

    @asynccontextmanager
    async def reserve_capacity(
        self,
        session_key: AgentSessionKey,
        max_sessions: int,
    ) -> AsyncGenerator[None]:
        """为新 Session 获取一个跨进程容量槽位。

        新 Session 在首个 Checkpoint 写入后进入持久化 namespace 列表。
        槽位从创建前持有到本次执行结束，将首次写入前的执行也计入并发容量。
        """
        namespaces = set(await self._repository.list_namespaces(None))
        if session_checkpoint_namespace(session_key) in namespaces:
            yield
            return
        if len(namespaces) >= max_sessions:
            raise RuntimeError("当前 Conversation 的 Session 数量已达上限")

        for slot in range(len(namespaces), max_sessions):
            async with AsyncExitStack() as stack:
                try:
                    await stack.enter_async_context(
                        self._locks.advisory_lock(
                            f"specialist-capacity:{self._thread_id}:{slot}"
                        )
                    )
                except AdvisoryLockBusyError:
                    continue
                # 只有争抢槽位失败才尝试下一槽位；执行体异常必须原样传播。
                yield
                return
        raise RuntimeError("当前 Conversation 的 Session 数量已达上限")
