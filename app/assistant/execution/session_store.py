"""专业 Agent Session 的持久化与工作区访问。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.assistant.checkpoints.postgres import PostgresCheckpointStore
from app.assistant.checkpoints.reader import (
    CheckpointState,
    CheckpointStateReader,
)
from app.assistant.execution.types import get_thread_id
from app.sandbox import DockerSandboxManager
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
from app.shared.contracts.analysis import AgentSessionKey
from app.shared.errors.infrastructure import AdvisoryLockBusyError


class PostgresSandboxSessionStore:
    """绑定一个 Conversation 的 Checkpoint、锁和 Sandbox 操作。"""

    def __init__(
        self,
        *,
        user_id: int,
        conversation_id: UUID,
        persistence: PostgresCheckpointStore,
        locks: PostgresAdvisoryLocks,
        checkpointer: AsyncPostgresSaver,
        sandbox: DockerSandboxManager,
    ) -> None:
        """初始化 Conversation 级状态访问上下文。"""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._thread_id = get_thread_id(user_id, conversation_id)
        self._persistence = persistence
        self._locks = locks
        self._sandbox = sandbox
        self._state_reader = CheckpointStateReader(checkpointer)

    async def list_namespaces(self, analysis_id: str | None) -> list[str]:
        """列出当前 Conversation 的专业 Session namespace。"""
        prefix = (
            f"subagents/{analysis_id}/" if analysis_id is not None else "subagents/"
        )
        return await self._persistence.list_checkpoint_namespaces(
            self._thread_id,
            prefix=prefix,
        )

    async def read_state(
        self,
        session_key: AgentSessionKey,
    ) -> CheckpointState:
        """读取专业 Session 的最新物化状态。"""
        return await self._state_reader.read(
            RunnableConfig(
                configurable={
                    "thread_id": self._thread_id,
                    "checkpoint_ns": session_key.checkpoint_ns,
                }
            )
        )

    async def delete_checkpoint(self, session_key: AgentSessionKey) -> bool:
        """删除专业 Session 的完整 Checkpoint namespace。"""
        return await self._persistence.delete_checkpoint_namespace(
            self._thread_id,
            session_key.checkpoint_ns,
        )

    async def delete_workspace(self, session_key: AgentSessionKey) -> bool:
        """删除专业 Session 的独立工作区。"""
        return await self._sandbox.delete_session(
            self._user_id,
            self._conversation_id,
            session_key.analysis_id,
            session_key.agent_type,
            session_key.session_id,
        )

    def lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        """获取专业 Session 的跨进程互斥锁。"""
        return self._locks.advisory_lock(
            f"specialist:{self._thread_id}:{session_key.checkpoint_ns}",
        )

    @asynccontextmanager
    async def reserve_capacity(
        self,
        session_key: AgentSessionKey,
        max_sessions: int,
    ) -> AsyncGenerator[None]:
        """为新 Session 获取一个跨进程容量槽位。

        新 Session 在首个 Checkpoint 写入前不会出现在持久化 namespace 列表中。
        槽位持有到本次执行结束，使并发进程也会计入这段空窗口。
        """
        namespaces = set(await self.list_namespaces(None))
        if session_key.checkpoint_ns in namespaces:
            yield
            return
        if len(namespaces) >= max_sessions:
            raise RuntimeError("当前 Conversation 的 Session 数量已达上限")

        for slot in range(len(namespaces), max_sessions):
            try:
                async with self._locks.advisory_lock(
                    f"specialist-capacity:{self._thread_id}:{slot}"
                ):
                    yield
                    return
            except AdvisoryLockBusyError:
                continue
        raise RuntimeError("当前 Conversation 的 Session 数量已达上限")
