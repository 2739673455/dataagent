"""专业 Agent Session 的持久化与工作区访问"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.analytics.agents.contracts import get_thread_id
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
)
from app.shared.contracts.analysis import AgentSessionKey


class AgentSessionStore(Protocol):
    """SessionService 使用的外部状态访问协议"""

    async def list_namespaces(self, analysis_id: str | None) -> list[str]: ...

    async def load_checkpoint(
        self,
        session_key: AgentSessionKey,
    ) -> Mapping[str, object] | None: ...

    async def delete_checkpoint(self, session_key: AgentSessionKey) -> bool: ...

    async def delete_workspace(self, session_key: AgentSessionKey) -> bool: ...

    async def artifact_exists(self, path: str) -> bool: ...

    def lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]: ...


class PostgresSandboxSessionStore:
    """绑定一个 Conversation 的 Checkpoint、锁和 Sandbox 操作"""

    def __init__(
        self,
        *,
        user_id: int,
        conversation_id: UUID,
        persistence: LangGraphPostgresManager,
        checkpointer: AsyncPostgresSaver,
        sandbox: DockerSandboxManager,
        conversation_backend: DockerSandboxBackend,
        lock_timeout: float,
    ) -> None:
        """初始化 Conversation 级状态访问上下文"""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._thread_id = get_thread_id(user_id, conversation_id)
        self._persistence = persistence
        self._checkpointer = checkpointer
        self._sandbox = sandbox
        self._conversation_backend = conversation_backend
        self._lock_timeout = lock_timeout

    async def list_namespaces(self, analysis_id: str | None) -> list[str]:
        """列出当前 Conversation 的专业 Session namespace"""
        prefix = (
            f"subagents/{analysis_id}/" if analysis_id is not None else "subagents/"
        )
        return await self._persistence.list_checkpoint_namespaces(
            self._thread_id,
            prefix=prefix,
        )

    async def load_checkpoint(
        self,
        session_key: AgentSessionKey,
    ) -> Mapping[str, object] | None:
        """读取专业 Session 的最新 Checkpoint"""
        checkpoint = await self._checkpointer.aget_tuple(
            RunnableConfig(
                configurable={
                    "thread_id": self._thread_id,
                    "checkpoint_ns": session_key.checkpoint_ns,
                }
            )
        )
        return checkpoint.checkpoint if checkpoint is not None else None

    async def delete_checkpoint(self, session_key: AgentSessionKey) -> bool:
        """删除专业 Session 的完整 Checkpoint namespace"""
        return await self._persistence.delete_checkpoint_namespace(
            self._thread_id,
            session_key.checkpoint_ns,
        )

    async def delete_workspace(self, session_key: AgentSessionKey) -> bool:
        """删除专业 Session 的独立工作区"""
        return await self._sandbox.delete_session(
            self._user_id,
            self._conversation_id,
            session_key.analysis_id,
            session_key.agent_type,
            session_key.session_id,
        )

    async def artifact_exists(self, path: str) -> bool:
        """验证产物文件存在于当前 Conversation 工作区"""
        relative_path = path.lstrip("/")
        result = await self._conversation_backend.aexecute(
            f"test -f {shlex.quote(relative_path)}",
            timeout=10,
        )
        return result.exit_code == 0

    def lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        """获取专业 Session 的跨进程互斥锁"""
        return self._persistence.advisory_lock(
            f"specialist:{self._thread_id}:{session_key.checkpoint_ns}",
            timeout=self._lock_timeout,
        )
