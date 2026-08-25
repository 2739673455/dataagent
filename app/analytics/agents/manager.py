"""Planner 与专业 Agent 的会话级生命周期管理"""

from __future__ import annotations

import asyncio
import shlex
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph

from app.analytics.agents.analyst.agent import create_analyst_agent
from app.analytics.agents.contracts import (
    PlannerTurnContext,
    SpecialistResult,
    get_thread_id,
)
from app.analytics.agents.explorer.agent import create_explorer_agent
from app.analytics.agents.explorer.tools import (
    create_execute_sql_tool,
    delete_semantic_recalls,
    get_semantic_recall,
    list_semantic_recalls,
    merge_semantic_recalls,
    search_query_experiences,
    search_semantic_resources,
)
from app.analytics.agents.mcp import get_mcp_tools
from app.analytics.agents.planner.agent import create_planner_agent
from app.analytics.agents.planner.tools import create_delegate_agent_tool
from app.analytics.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    build_agent_definitions,
)
from app.analytics.agents.reviewer.agent import create_reviewer_agent
from app.analytics.agents.session_service import AgentSessionService
from app.analytics.agents.visualizer.agent import create_visualizer_agent
from app.analytics.model_factory import create_configured_model
from app.query.providers import build_query_experience_service
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
)
from app.shared.clients.postgres_client_manager import meta_postgres_client_manager
from app.shared.config import app_config
from app.shared.contracts.analysis import AgentSessionKey, AgentType

type ConversationKey = tuple[int, UUID]

_DEFAULT_MAX_CACHED_RUNTIMES = 128
_STORE_SCAN_BATCH_SIZE = 1_000
_AGENT_LIFECYCLE_NAMESPACE = ("agent_lifecycle", "deleted_conversations")


def conversation_lifecycle_lock_name(user_id: int, conversation_id: UUID) -> str:
    """构造跨进程会话生命周期锁名称"""
    return f"conversation:{get_thread_id(user_id, conversation_id)}"


def _conversation_tombstone_key(user_id: int, conversation_id: UUID) -> str:
    """构造持久化删除墓碑键"""
    return f"{user_id}:{conversation_id}"


@dataclass(slots=True)
class ConversationAgentRuntime:
    """一个用户会话内的 Agent 运行时资源"""

    planner: CompiledStateGraph
    registry: AgentRegistry
    session_service: AgentSessionService
    session_locks: Mapping[str, asyncio.Lock]
    parallelism: asyncio.Semaphore
    planner_lock: Callable[[], AbstractAsyncContextManager[None]]
    conversation_deleted: Callable[[], Awaitable[bool]]


_SPECIALIST_BUILDERS = {
    "explorer": create_explorer_agent,
    "analyst": create_analyst_agent,
    "reviewer": create_reviewer_agent,
    "visualizer": create_visualizer_agent,
}


class AgentManager:
    """管理共享模型资源和会话级 Agent 运行时"""

    def __init__(
        self,
        persistence_manager: LangGraphPostgresManager,
        sandbox: DockerSandboxManager,
        max_cached_runtimes: int = _DEFAULT_MAX_CACHED_RUNTIMES,
    ) -> None:
        """初始化 Agent 管理器"""
        if max_cached_runtimes <= 0:
            raise ValueError("max_cached_runtimes 必须为正整数")
        self._persistence_manager = persistence_manager
        self._sandbox = sandbox
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
        self._models: dict[str, BaseChatModel] | None = None
        self._planner_model_name: str | None = None
        self._definitions: dict[AgentType, AgentDefinition] | None = None

    async def init(self) -> None:
        """初始化所有 Agent 共享的模型和工具"""
        async with self._state_lock:
            if self._models is not None and self._definitions is not None:
                return

            active_model_name = app_config.cfg.lm_config.active
            configured_names = {
                active_model_name,
                *(
                    active_model_name
                    if specialist.model == "default"
                    else specialist.model
                    for specialist in app_config.cfg.agent.specialists.values()
                ),
            }
            models = {
                model_name: create_configured_model(model_name)
                for model_name in configured_names
            }
            platform_tools: list[BaseTool] = [
                search_semantic_resources,
                list_semantic_recalls,
                get_semantic_recall,
                merge_semantic_recalls,
                delete_semantic_recalls,
                search_query_experiences,
                create_execute_sql_tool(self._sandbox),
            ]
            mcp_tools = await get_mcp_tools()
            definitions = build_agent_definitions(platform_tools, mcp_tools)
            self._models = models
            self._planner_model_name = active_model_name
            self._definitions = definitions

    async def get_active_model(self) -> BaseChatModel:
        """获取 Planner 使用的共享主模型"""
        await self.init()
        async with self._state_lock:
            if self._models is None or self._planner_model_name is None:
                raise RuntimeError("Agent 管理器尚未初始化")
            return self._models[self._planner_model_name]

    def _specialist_model(self, agent_type: AgentType) -> BaseChatModel:
        """解析专业 Agent 配置中的模型引用"""
        if self._models is None or self._planner_model_name is None:
            raise RuntimeError("Agent 管理器尚未初始化")
        configured_name = app_config.cfg.agent.specialists[agent_type].model
        model_name = (
            self._planner_model_name
            if configured_name == "default"
            else configured_name
        )
        return self._models[model_name]

    def _build_conversation_runtime(
        self,
        sandbox_backend: DockerSandboxBackend,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        """构建共享 Backend 和持久化组件的会话级 Agent 运行时"""
        if (
            self._models is None
            or self._planner_model_name is None
            or self._definitions is None
        ):
            raise RuntimeError("Agent 管理器尚未初始化")

        backend = sandbox_backend
        checkpointer = self._persistence_manager.get_checkpointer()
        store = self._persistence_manager.get_store()
        definitions = self._definitions

        async def build_session_agent(
            session_key: AgentSessionKey,
        ) -> CompiledStateGraph:
            """为指定专业 Agent Session 构建独立运行图"""
            session_backend = await self._sandbox.get_session_backend(
                session_key.user_id,
                session_key.conversation_id,
                session_key.analysis_id,
                session_key.agent_type,
                session_key.session_id,
            )
            definition = definitions[session_key.agent_type]
            builder = _SPECIALIST_BUILDERS[session_key.agent_type]
            return builder(
                model=self._specialist_model(session_key.agent_type),
                tools=definition.tools,
                backend=session_backend,
                checkpointer=checkpointer,
                store=store,
                skills=definition.skills,
            )

        registry = AgentRegistry(definitions, build_session_agent)
        orchestration_cfg = app_config.cfg.agent.orchestration

        async def observe_specialist_result(
            session_key: AgentSessionKey,
            result: SpecialistResult,
        ) -> None:
            """根据 Explorer 产物推进查询经验索引状态"""
            if session_key.agent_type != "explorer":
                return
            artifact_paths = {artifact.path for artifact in result.artifacts}
            async with meta_postgres_client_manager.session() as meta_session:
                service = build_query_experience_service(meta_session)
                await service.promote_by_artifacts(
                    user_id=session_key.user_id,
                    conversation_id=session_key.conversation_id,
                    analysis_id=session_key.analysis_id,
                    session_id=session_key.session_id,
                    artifact_paths=artifact_paths,
                )

        session_service = AgentSessionService(
            registry=registry,
            user_id=user_id,
            conversation_id=conversation_id,
            max_parallel_sessions=orchestration_cfg.max_parallel_sessions,
            max_delegations_per_run=orchestration_cfg.max_delegations_per_run,
            max_repair_rounds=orchestration_cfg.max_repair_rounds,
            max_repair_depth=orchestration_cfg.max_repair_depth,
            max_session_resumes=orchestration_cfg.max_session_resumes,
            session_lock_timeout=orchestration_cfg.session_lock_timeout,
            artifact_verifier=lambda path: self._artifact_exists(
                sandbox_backend,
                path,
            ),
            session_exists=lambda key: self._session_checkpoint_exists(
                checkpointer,
                key,
            ),
            session_lock_factory=lambda key: self._persistence_manager.advisory_lock(
                f"specialist:{get_thread_id(user_id, conversation_id)}:"
                f"{key.checkpoint_ns}",
                timeout=orchestration_cfg.session_lock_timeout,
            ),
            result_observer=observe_specialist_result,
        )
        delegate_agent = create_delegate_agent_tool(session_service)
        interpreter_cfg = app_config.cfg.agent.interpreter
        planner = create_planner_agent(
            model=self._models[self._planner_model_name],
            delegate_agent=delegate_agent,
            backend=backend,
            checkpointer=checkpointer,
            store=store,
            interpreter_mode=interpreter_cfg.mode,
            interpreter_ptc=interpreter_cfg.ptc,
            interpreter_timeout_seconds=interpreter_cfg.timeout_seconds,
            interpreter_memory_limit_bytes=interpreter_cfg.memory_limit_bytes,
            max_delegations_per_run=orchestration_cfg.max_delegations_per_run,
            max_repair_rounds=orchestration_cfg.max_repair_rounds,
            max_repair_depth=orchestration_cfg.max_repair_depth,
        )
        return ConversationAgentRuntime(
            planner=planner,
            registry=registry,
            session_service=session_service,
            session_locks=session_service.session_locks,
            parallelism=session_service.parallelism,
            planner_lock=lambda: self._persistence_manager.advisory_lock(
                conversation_lifecycle_lock_name(user_id, conversation_id),
                timeout=orchestration_cfg.session_lock_timeout,
            ),
            conversation_deleted=lambda: self._conversation_is_deleted(
                user_id,
                conversation_id,
            ),
        )

    @staticmethod
    async def _session_checkpoint_exists(
        checkpointer: AsyncPostgresSaver,
        session_key: AgentSessionKey,
    ) -> bool:
        """从 PostgreSQL Checkpointer 验证专业 Session 已存在"""
        checkpoint = await checkpointer.aget_tuple(
            RunnableConfig(
                configurable={
                    "thread_id": get_thread_id(
                        session_key.user_id,
                        session_key.conversation_id,
                    ),
                    "checkpoint_ns": session_key.checkpoint_ns,
                }
            )
        )
        return checkpoint is not None

    @staticmethod
    async def _artifact_exists(
        sandbox_backend: DockerSandboxBackend,
        path: str,
    ) -> bool:
        """在会话工作目录内验证产物文件存在"""
        relative_path = path.lstrip("/")
        result = await sandbox_backend.aexecute(
            f"test -f {shlex.quote(relative_path)}",
            timeout=10,
        )
        return result.exit_code == 0

    async def _build_and_cache_conversation_runtime(
        self,
        conversation_key: ConversationKey,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        """构建会话级 Agent 运行时并写入缓存"""
        current_task = asyncio.current_task()
        try:
            sandbox_backend = await self._sandbox.get_backend(
                user_id,
                conversation_id,
            )
            runtime = self._build_conversation_runtime(
                sandbox_backend,
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
                        evicted.registry.clear()
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

    async def reset(self) -> None:
        """清空缓存并按最新配置重新初始化共享资源"""
        await self.close()
        await self.init()

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
            timeout=app_config.cfg.agent.orchestration.session_lock_timeout,
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
            runtime.registry.clear()
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
        store = self._persistence_manager.get_store()
        tombstone_keys: list[str] = []
        offset = 0
        while items := await store.asearch(
            _AGENT_LIFECYCLE_NAMESPACE,
            limit=_STORE_SCAN_BATCH_SIZE,
            offset=offset,
        ):
            tombstone_keys.extend(
                item.key for item in items if item.key.startswith(f"{user_id}:")
            )
            offset += len(items)
        for key in tombstone_keys:
            await store.adelete(_AGENT_LIFECYCLE_NAMESPACE, key)
        async with self._state_lock:
            self._deleted_conversation_keys = {
                key for key in self._deleted_conversation_keys if key[0] != user_id
            }

    async def _conversation_is_deleted(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> bool:
        """从 Store 查询跨进程删除墓碑"""
        item = await self._persistence_manager.get_store().aget(
            _AGENT_LIFECYCLE_NAMESPACE,
            _conversation_tombstone_key(user_id, conversation_id),
        )
        return item is not None

    async def _mark_conversation_deleted(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """在删除 Checkpoint 前写入持久化墓碑"""
        await self._persistence_manager.get_store().aput(
            _AGENT_LIFECYCLE_NAMESPACE,
            _conversation_tombstone_key(user_id, conversation_id),
            {"deleted": True},
            index=False,
        )

    @asynccontextmanager
    async def execution(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        runtime: ConversationAgentRuntime,
    ) -> AsyncGenerator[PlannerTurnContext, None]:
        """登记完整用户回合并建立共享委派预算"""
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
            planner_run_id=uuid4().hex,
            max_continuations=(app_config.cfg.agent.orchestration.max_continuations),
        )
        try:
            async with (
                runtime.planner_lock(),
                runtime.session_service.planner_run(turn_context.planner_run_id),
            ):
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
            self._models = None
            self._planner_model_name = None
            self._definitions = None
        for build_task in build_tasks:
            build_task.cancel()
        for run_task in run_tasks:
            run_task.cancel()
        pending_tasks = [*build_tasks, *run_tasks]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        for runtime in runtimes:
            runtime.session_service.clear()
            runtime.registry.clear()
