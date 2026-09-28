"""Run 内 Agent 的创建与会话状态访问。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.types import StateSnapshot

from app.assistant.checkpoints.specialist import SpecialistCheckpointView
from app.assistant.conversations.tombstones import (
    ConversationTombstoneStore,
)
from app.assistant.execution.runtime_factory import ConversationAgentRuntimeFactory
from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.types import (
    ConversationAgentRuntime,
    DelegationActivityHistory,
    build_planner_config,
    get_thread_id,
)
from app.shared.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
)
from app.shared.contracts.analysis import AgentSessionKey, validate_agent_type

type ConversationKey = tuple[int, UUID]


class AgentManager:
    """按 Run 创建 Agent，并登记执行中的 Session 服务供状态查询。"""

    def __init__(
        self,
        persistence_manager: LangGraphPostgresManager,
        tombstones: ConversationTombstoneStore,
        runtime_factory: ConversationAgentRuntimeFactory,
    ) -> None:
        self._persistence_manager = persistence_manager
        self._tombstones = tombstones
        self._runtime_factory = runtime_factory
        self._active_sessions: dict[ConversationKey, AgentSessionService] = {}

    async def can_resume_planner(self, user_id: int, conversation_id: UUID) -> bool:
        """通过图状态判断 Planner 是否还有待执行节点。"""
        return bool((await self.read_planner_state(user_id, conversation_id)).next)

    async def read_planner_state(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> StateSnapshot:
        """编译 Planner 并读取原生图状态。"""
        if await self._tombstones.exists(user_id, conversation_id):
            raise RuntimeError("该会话已被删除")
        graph = self._runtime_factory.build(user_id, conversation_id).planner
        return await graph.aget_state(build_planner_config(user_id, conversation_id))

    async def read_delegation_activity(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        agent_type: str,
        session_id: str,
        delegation_id: str,
    ) -> DelegationActivityHistory | None:
        """直接从 Specialist Checkpoint 读取一次委派历史。"""
        session_key = AgentSessionKey(
            user_id=user_id,
            conversation_id=conversation_id,
            analysis_id=analysis_id,
            agent_type=validate_agent_type(agent_type),
            session_id=session_id,
        )
        graph = self._runtime_factory.specialists().build(session_key)
        state = await graph.aget_state(
            RunnableConfig(
                configurable={
                    "thread_id": session_key.thread_id,
                }
            )
        )
        sessions = self._active_sessions.get((user_id, conversation_id))
        active = bool(sessions and sessions.is_session_active(session_key.thread_id))
        return SpecialistCheckpointView(state.values).delegation_activity(
            delegation_id,
            active=active,
        )

    async def delete_agent_under_lifecycle_lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """在会话生命周期锁内写入删除标记并清理所有 Agent Checkpoint。"""
        # 先持久化墓碑再删除 Checkpoint，避免其他进程在删除窗口重建会话状态。
        await self._tombstones.save(user_id, conversation_id)
        thread_id = get_thread_id(user_id, conversation_id)
        for specialist_thread in await self._persistence_manager.list_threads(
            prefix=f"{thread_id}/subagents/"
        ):
            await self._persistence_manager.delete_thread(specialist_thread)
        await self._persistence_manager.delete_thread(thread_id)

    @asynccontextmanager
    async def use_runtime(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[ConversationAgentRuntime]:
        """调用方 Run 持有会话锁；创建、使用和释放都在同一个任务中完成。"""
        if await self._tombstones.exists(user_id, conversation_id):
            raise RuntimeError("该会话已被删除")
        runtime = await self._runtime_factory.create(user_id, conversation_id)
        key = (user_id, conversation_id)
        self._active_sessions[key] = runtime.session_service
        try:
            yield runtime
        finally:
            self._active_sessions.pop(key, None)
