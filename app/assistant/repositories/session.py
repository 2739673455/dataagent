"""专业 Agent Session 的检查点持久化访问。"""

from __future__ import annotations

from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.assistant.models.session import AgentSessionKey
from app.assistant.repositories.checkpoint import PostgresCheckpointStore
from app.assistant.repositories.checkpoint_reader import (
    CheckpointState,
    CheckpointStateReader,
)
from app.assistant.sessions.identity import (
    get_thread_id,
    session_checkpoint_namespace,
)


class SessionCheckpointRepository:
    """读取和删除一个 Conversation 下的专业 Session 检查点。"""

    def __init__(
        self,
        *,
        user_id: int,
        conversation_id: UUID,
        persistence: PostgresCheckpointStore,
        checkpointer: AsyncPostgresSaver,
    ) -> None:
        """初始化 Conversation 级状态访问上下文。"""
        self._thread_id = get_thread_id(user_id, conversation_id)
        self._persistence = persistence
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
                    "checkpoint_ns": session_checkpoint_namespace(session_key),
                }
            )
        )

    async def delete_checkpoint(self, session_key: AgentSessionKey) -> bool:
        """删除专业 Session 的完整 Checkpoint namespace。"""
        return await self._persistence.delete_checkpoint_namespace(
            self._thread_id,
            session_checkpoint_namespace(session_key),
        )
