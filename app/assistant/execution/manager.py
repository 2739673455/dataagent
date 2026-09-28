"""Planner 与专业 Agent 的会话级生命周期管理。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
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

_DEFAULT_MAX_CACHED_RUNTIMES = 128


class AgentManager:
    """管理 Conversation Agent 运行时的构建、借用、缓存和删除。"""

    def __init__(
        self,
        persistence_manager: LangGraphPostgresManager,
        tombstones: ConversationTombstoneStore,
        runtime_factory: ConversationAgentRuntimeFactory | None = None,
        max_cached_runtimes: int = _DEFAULT_MAX_CACHED_RUNTIMES,
    ) -> None:
        """初始化 Agent 管理器。"""
        if max_cached_runtimes <= 0:
            raise ValueError("max_cached_runtimes 必须为正整数")
        self._persistence_manager = persistence_manager
        self._tombstones = tombstones
        self._runtime_factory = runtime_factory
        self._max_cached_runtimes = max_cached_runtimes
        self._conversation_runtimes: OrderedDict[
            ConversationKey, ConversationAgentRuntime
        ] = OrderedDict()
        self._runtime_build_tasks: dict[
            ConversationKey, asyncio.Task[ConversationAgentRuntime]
        ] = {}
        self._runtime_users: dict[ConversationKey, int] = {}
        self._deleted_conversation_keys: set[ConversationKey] = set()
        self._state_lock = asyncio.Lock()

    async def _build_and_cache_conversation_runtime(
        self,
        conversation_key: ConversationKey,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        """构建会话级 Agent 运行时并写入缓存。"""
        current_task = asyncio.current_task()
        try:
            if self._runtime_factory is None:
                raise RuntimeError("清理任务未配置 Agent 执行能力")
            runtime = await self._runtime_factory.create(
                user_id,
                conversation_id,
            )
        except (Exception, asyncio.CancelledError):
            async with self._state_lock:
                if self._runtime_build_tasks.get(conversation_key) is current_task:
                    self._runtime_build_tasks.pop(conversation_key, None)
            raise

        evicted_runtimes: list[ConversationAgentRuntime] = []
        discarded = False
        async with self._state_lock:
            if self._runtime_build_tasks.get(conversation_key) is current_task:
                self._runtime_build_tasks.pop(conversation_key, None)
                if conversation_key not in self._deleted_conversation_keys:
                    self._conversation_runtimes[conversation_key] = runtime
                    self._conversation_runtimes.move_to_end(conversation_key)
                    while len(self._conversation_runtimes) > self._max_cached_runtimes:
                        evictable_key = next(
                            (
                                key
                                for key in self._conversation_runtimes
                                if key != conversation_key
                                and not self._runtime_users.get(key)
                            ),
                            None,
                        )
                        if evictable_key is None:
                            break
                        evicted = self._conversation_runtimes.pop(evictable_key)
                        evicted_runtimes.append(evicted)
            if self._conversation_runtimes.get(conversation_key) is not runtime:
                discarded = True
                evicted_runtimes.append(runtime)
        for evicted in evicted_runtimes:
            evicted.session_service.clear()
        if discarded:
            raise RuntimeError("运行时构建已失效")
        return runtime

    async def _get_conversation_runtime(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        """获取会话级 Agent 运行时，不存在时按需创建。"""
        conversation_key = (user_id, conversation_id)
        if await self._tombstones.exists(user_id, conversation_id):
            async with self._state_lock:
                self._deleted_conversation_keys.add(conversation_key)
            raise RuntimeError("该会话已被删除")
        async with self._state_lock:
            if conversation_key in self._deleted_conversation_keys:
                raise RuntimeError("该会话已被删除")
            if runtime := self._conversation_runtimes.get(conversation_key):
                self._conversation_runtimes.move_to_end(conversation_key)
                return runtime
            build_task = self._runtime_build_tasks.get(conversation_key)
            if build_task is None:
                build_task = asyncio.create_task(
                    self._build_and_cache_conversation_runtime(
                        conversation_key,
                        user_id,
                        conversation_id,
                    )
                )
                self._runtime_build_tasks[conversation_key] = build_task
        return await asyncio.shield(build_task)

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
        if self._runtime_factory is None:
            raise RuntimeError("未配置 Agent 图工厂")
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
        if self._runtime_factory is None:
            raise RuntimeError("未配置 Agent 图工厂")
        graph = self._runtime_factory.specialists().build(session_key)
        state = await graph.aget_state(
            RunnableConfig(
                configurable={
                    "thread_id": session_key.thread_id,
                }
            )
        )
        async with self._state_lock:
            runtime = self._conversation_runtimes.get((user_id, conversation_id))
            active = bool(
                runtime
                and runtime.session_service.is_session_active(session_key.thread_id)
            )
        return SpecialistCheckpointView(state.values).delegation_activity(
            delegation_id,
            active=active,
        )

    async def delete_agent_under_lifecycle_lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """在调用方持有会话生命周期锁时删除 Agent 和持久化状态。"""
        conversation_key = (user_id, conversation_id)
        async with self._state_lock:
            self._deleted_conversation_keys.add(conversation_key)
            build_task = self._runtime_build_tasks.pop(conversation_key, None)
        if build_task is not None:
            build_task.cancel()
            await asyncio.gather(build_task, return_exceptions=True)
        async with self._state_lock:
            runtime = self._conversation_runtimes.pop(conversation_key, None)
        if runtime is not None:
            runtime.session_service.clear()
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
        """从构建等待开始保护运行时；执行 Task 和互斥锁由 Run 持有。"""
        key = (user_id, conversation_id)
        async with self._state_lock:
            if key in self._deleted_conversation_keys:
                raise RuntimeError("该会话已被删除")
            self._runtime_users[key] = self._runtime_users.get(key, 0) + 1
        try:
            runtime = await self._get_conversation_runtime(user_id, conversation_id)
            if await self._tombstones.exists(user_id, conversation_id):
                raise RuntimeError("该会话已被删除")
            yield runtime
        finally:
            async with self._state_lock:
                remaining = self._runtime_users[key] - 1
                if remaining:
                    self._runtime_users[key] = remaining
                else:
                    self._runtime_users.pop(key)

    async def close(self) -> None:
        """释放 Agent 集合和未完成任务。"""
        async with self._state_lock:
            build_tasks = list(self._runtime_build_tasks.values())
            runtimes = list(self._conversation_runtimes.values())
            self._runtime_build_tasks.clear()
            self._conversation_runtimes.clear()
        for build_task in build_tasks:
            build_task.cancel()
        if build_tasks:
            await asyncio.gather(*build_tasks, return_exceptions=True)
        for runtime in runtimes:
            runtime.session_service.clear()
        if self._runtime_factory is not None:
            await self._runtime_factory.close()
