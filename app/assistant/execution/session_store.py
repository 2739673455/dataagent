"""专业 Agent Session 的持久化与工作区访问。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

from app.assistant.execution.types import get_thread_id
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.contracts.analysis import AgentSessionKey


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
        build_agent: Callable[[AgentSessionKey], CompiledStateGraph],
    ) -> None:
        """初始化 Conversation 级状态访问上下文。"""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._thread_id = get_thread_id(user_id, conversation_id)
        self._persistence = persistence
        self._sandbox = sandbox
        self._checkpointer = checkpointer
        self._build_agent = build_agent

    async def list_threads(self, analysis_id: str | None) -> list[str]:
        """列出当前 Conversation 的专业 Session 线程。"""
        prefix = f"{self._thread_id}/subagents/" + (
            f"{analysis_id}/" if analysis_id is not None else ""
        )
        return await self._persistence.list_threads(prefix=prefix)

    async def read_state(
        self,
        session_key: AgentSessionKey,
    ) -> StateSnapshot:
        """读取专业 Session 的最新物化状态。"""
        return await self._build_agent(session_key).aget_state(
            RunnableConfig(
                configurable={
                    "thread_id": session_key.thread_id,
                }
            )
        )

    async def delete_checkpoint(self, session_key: AgentSessionKey) -> bool:
        """删除专业 Session 的完整 Checkpoint 线程。"""
        config = RunnableConfig(configurable={"thread_id": session_key.thread_id})
        existed = await self._checkpointer.aget_tuple(config) is not None
        await self._checkpointer.adelete_thread(session_key.thread_id)
        return existed

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
        return self._persistence.advisory_lock(
            f"specialist:{session_key.thread_id}",
        )
