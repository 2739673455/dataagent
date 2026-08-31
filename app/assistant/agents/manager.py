"""Planner 与专业 Agent 的会话级生命周期管理"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from app.assistant.agents.contracts import (
    ConversationAgentRuntime,
    PlannerTurnContext,
    conversation_lifecycle_lock_name,
    get_thread_id,
)
from app.assistant.agents.runtime_factory import ConversationAgentRuntimeFactory
from app.assistant.services.conversation_tombstone import (
    ConversationTombstoneService,
)
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
)
from app.shared.config import app_config

type ConversationKey = tuple[int, UUID]

_DEFAULT_MAX_CACHED_RUNTIMES = 128


class AgentManager:
    """管理 Conversation Agent 运行时的缓存、执行和删除生命周期"""

    def __init__(
        self,
        persistence_manager: LangGraphPostgresManager,
        sandbox: DockerSandboxManager,
        tombstones: ConversationTombstoneService,
        max_cached_runtimes: int = _DEFAULT_MAX_CACHED_RUNTIMES,
    ) -> None:
        """初始化 Agent 管理器"""
        if max_cached_runtimes <= 0:
            raise ValueError("max_cached_runtimes 必须为正整数")
        self._persistence_manager = persistence_manager
        self._tombstones = tombstones
        self._runtime_factory = ConversationAgentRuntimeFactory(
            persistence_manager,
            sandbox,
            tombstones,
        )
        self._max_cached_runtimes = max_cached_runtimes
        self._conversation_runtimes: OrderedDict[
            ConversationKey, ConversationAgentRuntime
        ] = OrderedDict()
        self._runtime_build_tasks: dict[
            ConversationKey, asyncio.Task[ConversationAgentRuntime]
        ] = {}
        self._conversation_run_tasks: dict[
            ConversationKey, set[asyncio.Task[object]]
        ] = {}
        self._deleted_conversation_keys: set[ConversationKey] = set()
        self._state_lock = asyncio.Lock()

    async def init(self) -> None:
        """初始化运行时工厂持有的共享模型和工具"""
        await self._runtime_factory.init()

    async def _build_and_cache_conversation_runtime(
        self,
        conversation_key: ConversationKey,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        """构建会话级 Agent 运行时并写入缓存"""
        current_task = asyncio.current_task()
        try:
            runtime = await self._runtime_factory.create(
                user_id,
                conversation_id,
            )
        except (Exception, asyncio.CancelledError):
            async with self._state_lock:
                if self._runtime_build_tasks.get(conversation_key) is current_task:
                    self._runtime_build_tasks.pop(conversation_key, None)
            raise

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
                                and not self._conversation_run_tasks.get(key)
                            ),
                            None,
                        )
                        if evictable_key is None:
                            break
                        evicted = self._conversation_runtimes.pop(evictable_key)
                        evicted.session_service.clear()
        return runtime

    async def get_conversation_runtime(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        """获取会话级 Agent 运行时，不存在时按需创建"""
        await self.init()
        conversation_key = (user_id, conversation_id)
        if await self._conversation_is_deleted(user_id, conversation_id):
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

    async def cancel_agent_execution(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """阻止新回合并取消当前进程中的会话任务"""
        conversation_key = (user_id, conversation_id)
        async with self._state_lock:
            self._deleted_conversation_keys.add(conversation_key)
            build_task = self._runtime_build_tasks.pop(conversation_key, None)
            run_tasks = list(self._conversation_run_tasks.pop(conversation_key, ()))
        if build_task is not None:
            build_task.cancel()
            await asyncio.gather(build_task, return_exceptions=True)
        for run_task in run_tasks:
            run_task.cancel()
        if run_tasks:
            await asyncio.gather(*run_tasks, return_exceptions=True)

    async def delete_agent(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话 Agent 集合及 Planner 和全部 SubAgent namespace"""
        await self.cancel_agent_execution(user_id, conversation_id)
        async with self._persistence_manager.advisory_lock(
            conversation_lifecycle_lock_name(user_id, conversation_id),
        ):
            await self.delete_agent_under_lifecycle_lock(user_id, conversation_id)

    async def delete_agent_under_lifecycle_lock(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """在调用方持有会话生命周期锁时删除 Agent 和持久化状态"""
        conversation_key = (user_id, conversation_id)
        await self.cancel_agent_execution(user_id, conversation_id)
        async with self._state_lock:
            runtime = self._conversation_runtimes.pop(conversation_key, None)
        if runtime is not None:
            runtime.session_service.clear()
        await self._mark_conversation_deleted(user_id, conversation_id)
        await self._persistence_manager.delete_thread(
            get_thread_id(user_id, conversation_id)
        )

    async def delete_user_agents(self, user_id: int) -> None:
        """取消用户全部 Agent 并清理孤立线程和删除墓碑"""
        async with self._state_lock:
            conversation_keys = {
                key
                for key in (
                    set(self._conversation_runtimes)
                    | set(self._runtime_build_tasks)
                    | set(self._conversation_run_tasks)
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

    async def _conversation_is_deleted(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> bool:
        """从关系表查询跨进程删除墓碑"""
        return await self._tombstones.exists(user_id, conversation_id)

    async def _mark_conversation_deleted(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """在删除 Checkpoint 前写入持久化墓碑"""
        await self._tombstones.save(user_id, conversation_id)

    @asynccontextmanager
    async def execution(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        runtime: ConversationAgentRuntime,
    ) -> AsyncGenerator[PlannerTurnContext, None]:
        """登记完整用户回合并建立共享运行状态"""
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("Agent 执行必须在 asyncio 任务上下文中进行")
        conversation_key = (user_id, conversation_id)
        async with self._state_lock:
            if conversation_key in self._deleted_conversation_keys:
                raise RuntimeError("该会话已被删除")
            self._conversation_run_tasks.setdefault(conversation_key, set()).add(
                current_task
            )
        turn_context = PlannerTurnContext(
            user_id=user_id,
            conversation_id=conversation_id,
            max_continuations=(app_config.cfg.agent.orchestration.max_continuations),
        )
        try:
            async with runtime.planner_lock():
                if await runtime.conversation_deleted():
                    raise RuntimeError("该会话已被删除")
                yield turn_context
        finally:
            async with self._state_lock:
                tasks = self._conversation_run_tasks.get(conversation_key)
                if tasks is not None:
                    tasks.discard(current_task)
                    if not tasks:
                        self._conversation_run_tasks.pop(conversation_key, None)

    async def close(self) -> None:
        """释放 Agent 集合和未完成任务"""
        async with self._state_lock:
            build_tasks = list(self._runtime_build_tasks.values())
            run_tasks = [
                task
                for tasks in self._conversation_run_tasks.values()
                for task in tasks
            ]
            runtimes = list(self._conversation_runtimes.values())
            self._runtime_build_tasks.clear()
            self._conversation_run_tasks.clear()
            self._conversation_runtimes.clear()
        for build_task in build_tasks:
            build_task.cancel()
        for run_task in run_tasks:
            run_task.cancel()
        pending_tasks = [*build_tasks, *run_tasks]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        for runtime in runtimes:
            runtime.session_service.clear()
        self._runtime_factory.close()
