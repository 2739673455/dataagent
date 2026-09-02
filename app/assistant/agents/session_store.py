"""专业 Agent Session 的持久化与工作区访问。"""

from __future__ import annotations

import shlex
from collections.abc import AsyncGenerator, Collection
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.assistant.agents.checkpoint_reader import (
    CheckpointState,
    CheckpointStateReader,
)
from app.assistant.agents.contracts import get_thread_id
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import (
    AdvisoryLockBusyError,
    LangGraphPostgresManager,
)
from app.shared.contracts.analysis import AgentSessionKey


class AgentSessionStore(Protocol):
    """SessionService 使用的外部状态访问协议。"""

    async def list_namespaces(self, analysis_id: str | None) -> list[str]:
        """列出指定 Analysis 或整个 Conversation 的 Session namespace。"""
        ...

    async def read_state(
        self,
        session_key: AgentSessionKey,
    ) -> CheckpointState:
        """读取指定 Session 的最新物化状态。"""
        ...

    async def delete_checkpoint(self, session_key: AgentSessionKey) -> bool:
        """删除指定 Session 的完整 Checkpoint namespace。"""
        ...

    async def delete_workspace(self, session_key: AgentSessionKey) -> bool:
        """删除指定 Session 的独立工作区。"""
        ...

    async def find_missing_files(self, paths: Collection[str]) -> set[str]:
        """返回当前 Conversation 工作区中不存在的文件路径。"""
        ...

    def lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        """创建指定 Session 的非阻塞执行锁上下文。"""
        ...

    def reserve_capacity(
        self,
        session_key: AgentSessionKey,
        max_sessions: int,
    ) -> AbstractAsyncContextManager[None]:
        """为尚未持久化的新 Session 保留跨进程容量。"""
        ...


class PostgresSandboxSessionStore:
    """绑定一个 Conversation 的 Checkpoint、锁和 Sandbox 操作。"""

    def __init__(
        self,
        *,
        user_id: int,
        conversation_id: UUID,
        persistence: LangGraphPostgresManager,
        checkpointer: AsyncPostgresSaver,
        sandbox: DockerSandboxManager,
        conversation_backend: DockerSandboxBackend,
    ) -> None:
        """初始化 Conversation 级状态访问上下文。"""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._thread_id = get_thread_id(user_id, conversation_id)
        self._persistence = persistence
        self._sandbox = sandbox
        self._conversation_backend = conversation_backend
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

    async def find_missing_files(self, paths: Collection[str]) -> set[str]:
        """批量返回当前 Conversation 工作区中不存在的文件。"""
        if not paths:
            return set()
        arguments = " ".join(shlex.quote(path) for path in sorted(paths))
        command = (
            f"set -- {arguments}; "
            'for path do [ -f "$path" ] || printf \'%s\\0\' "$path"; done'
        )
        result = await self._conversation_backend.aexecute(
            command,
            timeout=10,
        )
        if result.exit_code != 0 or result.truncated:
            raise RuntimeError("批量验证产物文件失败")
        return {path for path in result.output.split("\0") if path}

    def lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        """获取专业 Session 的跨进程互斥锁。"""
        return self._persistence.advisory_lock(
            f"specialist:{self._thread_id}:{session_key.checkpoint_ns}",
        )

    @asynccontextmanager
    async def reserve_capacity(
        self,
        session_key: AgentSessionKey,
        max_sessions: int,
    ) -> AsyncGenerator[None, None]:
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
                async with self._persistence.advisory_lock(
                    f"specialist-capacity:{self._thread_id}:{slot}"
                ):
                    yield
                    return
            except AdvisoryLockBusyError:
                continue
        raise RuntimeError("当前 Conversation 的 Session 数量已达上限")
