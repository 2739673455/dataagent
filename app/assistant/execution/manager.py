"""Planner 与专业 Agent 的会话级生命周期管理。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from langchain_core.runnables import RunnableConfig

from app.assistant.checkpoints.reader import (
    CheckpointState,
    CheckpointStateReader,
)
from app.assistant.checkpoints.specialist import SpecialistCheckpointView
from app.assistant.conversations.tombstones import (
    ConversationTombstoneStore,
)
from app.assistant.execution.runtime_factory import ConversationAgentRuntimeFactory
from app.assistant.execution.types import (
    ConversationAgentRuntime,
    DelegationActivityHistory,
    build_planner_config,
    conversation_lifecycle_lock_name,
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

    async def init(self) -> None:
        """初始化运行时工厂持有的共享模型和工具。"""
        if self._runtime_factory is None:
            raise RuntimeError("清理任务未配置 Agent 执行能力")
        await self._runtime_factory.init()

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
            await evicted.shell_jobs.cleanup()
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
        await self.init()
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
        """检查 Planner 待执行任务，不还原历史消息或创建运行时。"""
        if await self._tombstones.exists(user_id, conversation_id):
            raise RuntimeError("该会话已被删除")
        reader = CheckpointStateReader(self._persistence_manager.get_checkpointer())
        return await reader.has_pending_tasks(
            build_planner_config(user_id, conversation_id)
        )

    async def read_planner_state(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> CheckpointState:
        """读取 Planner 根 namespace，且不创建 Conversation 运行时。"""
        if await self._tombstones.exists(user_id, conversation_id):
            raise RuntimeError("该会话已被删除")
        reader = CheckpointStateReader(self._persistence_manager.get_checkpointer())
        return await reader.read(build_planner_config(user_id, conversation_id))

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
        reader = CheckpointStateReader(self._persistence_manager.get_checkpointer())
        state = await reader.read(
            RunnableConfig(
                configurable={
                    "thread_id": get_thread_id(user_id, conversation_id),
                    "checkpoint_ns": session_key.checkpoint_ns,
                }
            )
        )
        async with self._state_lock:
            runtime = self._conversation_runtimes.get((user_id, conversation_id))
            active = bool(
                runtime
                and runtime.session_service.is_session_active(session_key.checkpoint_ns)
            )
        return SpecialistCheckpointView(state.values).delegation_activity(
            delegation_id,
            active=active,
        )

    async def _cancel_runtime_build(self, conversation_key: ConversationKey) -> None:
        """删除已受理后禁止重建，并回收尚未结束的构建任务。"""
        async with self._state_lock:
            self._deleted_conversation_keys.add(conversation_key)
            build_task = self._runtime_build_tasks.pop(conversation_key, None)
        if build_task is not None:
            build_task.cancel()
            await asyncio.gather(build_task, return_exceptions=True)

    async def delete_agent(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话 Agent 集合及 Planner 和全部 SubAgent namespace。"""
        async with self._persistence_manager.advisory_lock(
            conversation_lifecycle_lock_name(user_id, conversation_id),
        ):
            await self.delete_agent_under_lifecycle_lock(user_id, conversation_id)

    async def delete_agent_under_lifecycle_lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """在调用方持有会话生命周期锁时删除 Agent 和持久化状态。"""
        conversation_key = (user_id, conversation_id)
        await self._cancel_runtime_build(conversation_key)
        async with self._state_lock:
            runtime = self._conversation_runtimes.pop(conversation_key, None)
        if runtime is not None:
            runtime.session_service.clear()
            await runtime.shell_jobs.cleanup()
        # 先持久化墓碑再删除 Checkpoint，避免其他进程在删除窗口重建会话状态。
        await self._tombstones.save(user_id, conversation_id)
        await self._persistence_manager.delete_thread(
            get_thread_id(user_id, conversation_id)
        )

    async def delete_user_agents(self, user_id: int) -> None:
        """清理用户全部 Agent、孤立线程和删除墓碑。"""
        async with self._state_lock:
            conversation_keys = {
                key
                for key in (
                    set(self._conversation_runtimes)
                    | set(self._runtime_build_tasks)
                    | set(self._runtime_users)
                )
                if key[0] == user_id
            }
        for _, conversation_id in sorted(
            conversation_keys,
            key=lambda item: str(item[1]),
        ):
            await self.delete_agent(user_id, conversation_id)

        await self._persistence_manager.delete_user_threads(user_id)
        await self._tombstones.delete_by_user(user_id)
        async with self._state_lock:
            self._deleted_conversation_keys = {
                key for key in self._deleted_conversation_keys if key[0] != user_id
            }

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
        await asyncio.gather(
            *(runtime.shell_jobs.cleanup() for runtime in runtimes),
            return_exceptions=True,
        )
        if self._runtime_factory is not None:
            await self._runtime_factory.close()
