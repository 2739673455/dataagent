"""Planner 与专业 Agent 的会话级生命周期管理"""

from __future__ import annotations

import asyncio
import shlex
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph

from app.agents.anomaly_detection.agent import create_anomaly_detection_agent
from app.agents.attribution.agent import create_attribution_agent
from app.agents.contracts import (
    AgentSessionKey,
    AgentType,
    PlannerTurnContext,
    get_thread_id,
)
from app.agents.data_query.agent import create_data_query_agent
from app.agents.data_query.tools import (
    check_sql_syntax,
    delete_semantic_recalls,
    get_semantic_recall,
    list_semantic_recalls,
    merge_semantic_recalls,
    run_readonly_sql,
    search_semantic_resources,
)
from app.agents.mcp import get_mcp_tools
from app.agents.planner.agent import create_planner_agent
from app.agents.planner.tools import create_delegate_agent_tool
from app.agents.registry import AgentRegistry, build_agent_definitions
from app.agents.session_service import AgentSessionService
from app.agents.visualization.agent import create_visualization_agent
from app.clients.docker_sandbox_manager import (
    DockerSandboxBackend,
    docker_sandbox_manager,
)
from app.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
    langgraph_postgres_manager,
)
from app.conf import app_config

type AgentKey = tuple[int, UUID]

_DEFAULT_MAX_CACHED_AGENTS = 128
_AGENT_LIFECYCLE_NAMESPACE = ("agent_lifecycle", "deleted_conversations")


def _conversation_lock_name(user_id: int, conversation_id: UUID) -> str:
    """构造跨进程会话生命周期锁名称"""
    return f"conversation:{get_thread_id(user_id, conversation_id)}"


def _conversation_tombstone_key(user_id: int, conversation_id: UUID) -> str:
    """构造持久化删除墓碑键"""
    return f"{user_id}:{conversation_id}"


@dataclass(slots=True)
class AnalysisAgentBundle:
    """一个用户会话内共享资源的 Planner 和专业 Agent 集合"""

    planner: CompiledStateGraph
    registry: AgentRegistry
    session_service: AgentSessionService
    session_locks: Mapping[str, asyncio.Lock]
    parallelism: asyncio.Semaphore
    planner_lock: Callable[[], AbstractAsyncContextManager[None]]
    conversation_deleted: Callable[[], Awaitable[bool]]


_SPECIALIST_BUILDERS = {
    "data_query": create_data_query_agent,
    "attribution": create_attribution_agent,
    "anomaly_detection": create_anomaly_detection_agent,
    "visualization": create_visualization_agent,
}


class AgentManager:
    """管理共享模型资源和会话级 AnalysisAgentBundle"""

    def __init__(
        self,
        persistence_manager: LangGraphPostgresManager,
        max_cached_agents: int = _DEFAULT_MAX_CACHED_AGENTS,
    ) -> None:
        """初始化 Agent 管理器"""
        if max_cached_agents <= 0:
            raise ValueError("max_cached_agents must be positive")
        self._persistence_manager = persistence_manager
        self._max_cached_agents = max_cached_agents
        self._bundles: OrderedDict[AgentKey, AnalysisAgentBundle] = OrderedDict()
        self._build_tasks: dict[AgentKey, asyncio.Task[AnalysisAgentBundle]] = {}
        self._run_tasks: dict[AgentKey, set[asyncio.Task[object]]] = {}
        self._deleted_agent_keys: set[AgentKey] = set()
        self._state_lock = asyncio.Lock()
        self._models: dict[str, BaseChatModel] | None = None
        self._planner_model_name: str | None = None
        self._tools: list[BaseTool] | None = None

    @staticmethod
    def _create_model(model_name: str) -> BaseChatModel:
        """按配置名称初始化一个共享聊天模型"""
        try:
            model_cfg = app_config.cfg.lm_config.models[model_name]
        except KeyError as exc:
            raise ValueError(f"unknown language model config: {model_name}") from exc
        register_harness_profile(
            f"{model_cfg.model_provider}:{model_cfg.model}",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            ),
        )
        return init_chat_model(
            model_provider=model_cfg.model_provider,
            model=model_cfg.model,
            base_url=model_cfg.base_url,
            api_key=model_cfg.api_key,
            profile=model_cfg.profile,
            request_timeout=30,
            max_retries=2,
            **model_cfg.params,
        )

    async def init(self) -> None:
        """初始化所有 Agent 共享的模型和工具"""
        async with self._state_lock:
            if self._models is not None and self._tools is not None:
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
                model_name: self._create_model(model_name)
                for model_name in configured_names
            }
            tools: list[BaseTool] = [
                search_semantic_resources,
                list_semantic_recalls,
                get_semantic_recall,
                merge_semantic_recalls,
                delete_semantic_recalls,
                check_sql_syntax,
                run_readonly_sql,
                *await get_mcp_tools(),
            ]
            self._models = models
            self._planner_model_name = active_model_name
            self._tools = tools

    def _specialist_model(self, agent_type: AgentType) -> BaseChatModel:
        """解析专业 Agent 配置中的模型引用"""
        if self._models is None or self._planner_model_name is None:
            raise RuntimeError("Agent manager is not initialized")
        configured_name = app_config.cfg.agent.specialists[agent_type].model
        model_name = (
            self._planner_model_name
            if configured_name == "default"
            else configured_name
        )
        return self._models[model_name]

    def _build_bundle(
        self,
        sandbox_backend: DockerSandboxBackend,
        user_id: int,
        conversation_id: UUID,
    ) -> AnalysisAgentBundle:
        """构建共享 Backend 和持久化组件的会话级 Agent 集合"""
        if (
            self._models is None
            or self._planner_model_name is None
            or self._tools is None
        ):
            raise RuntimeError("Agent manager is not initialized")

        backend = sandbox_backend
        checkpointer = self._persistence_manager.get_checkpointer()
        store = self._persistence_manager.get_store()
        definitions = build_agent_definitions(self._tools)

        async def build_session_agent(
            session_key: AgentSessionKey,
        ) -> CompiledStateGraph:
            session_backend = await docker_sandbox_manager.get_session_backend(
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
        return AnalysisAgentBundle(
            planner=planner,
            registry=registry,
            session_service=session_service,
            session_locks=session_service.session_locks,
            parallelism=session_service.parallelism,
            planner_lock=lambda: self._persistence_manager.advisory_lock(
                _conversation_lock_name(user_id, conversation_id),
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

    async def _build_and_cache_bundle(
        self,
        agent_key: AgentKey,
        user_id: int,
        conversation_id: UUID,
    ) -> AnalysisAgentBundle:
        """构建 AnalysisAgentBundle 并写入会话缓存"""
        current_task = asyncio.current_task()
        try:
            sandbox_backend = await docker_sandbox_manager.get_backend(
                user_id,
                conversation_id,
            )
            bundle = self._build_bundle(
                sandbox_backend,
                user_id,
                conversation_id,
            )
        except (Exception, asyncio.CancelledError):
            async with self._state_lock:
                if self._build_tasks.get(agent_key) is current_task:
                    self._build_tasks.pop(agent_key, None)
            raise

        async with self._state_lock:
            if self._build_tasks.get(agent_key) is current_task:
                self._build_tasks.pop(agent_key, None)
                if agent_key not in self._deleted_agent_keys:
                    self._bundles[agent_key] = bundle
                    self._bundles.move_to_end(agent_key)
                    while len(self._bundles) > self._max_cached_agents:
                        evictable_key = next(
                            (
                                key
                                for key in self._bundles
                                if key != agent_key and not self._run_tasks.get(key)
                            ),
                            None,
                        )
                        if evictable_key is None:
                            break
                        evicted = self._bundles.pop(evictable_key)
                        evicted.session_service.clear()
                        evicted.registry.clear()
        return bundle

    async def get_agent_bundle(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AnalysisAgentBundle:
        """获取会话级 AnalysisAgentBundle，不存在时按需创建"""
        await self.init()
        agent_key = (user_id, conversation_id)
        if await self._conversation_is_deleted(user_id, conversation_id):
            async with self._state_lock:
                self._deleted_agent_keys.add(agent_key)
            raise RuntimeError("Agent conversation has been deleted")
        async with self._state_lock:
            if agent_key in self._deleted_agent_keys:
                raise RuntimeError("Agent conversation has been deleted")
            if bundle := self._bundles.get(agent_key):
                self._bundles.move_to_end(agent_key)
                return bundle
            build_task = self._build_tasks.get(agent_key)
            if build_task is None:
                build_task = asyncio.create_task(
                    self._build_and_cache_bundle(
                        agent_key,
                        user_id,
                        conversation_id,
                    )
                )
                self._build_tasks[agent_key] = build_task
        return await asyncio.shield(build_task)

    async def reset(self) -> None:
        """清空缓存并按最新配置重新初始化共享资源"""
        await self.close()
        await self.init()

    async def delete_agent(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话 Agent 集合及 Planner 和全部 SubAgent namespace"""
        agent_key = (user_id, conversation_id)
        async with self._state_lock:
            self._deleted_agent_keys.add(agent_key)
            bundle = self._bundles.pop(agent_key, None)
            build_task = self._build_tasks.pop(agent_key, None)
            run_tasks = list(self._run_tasks.pop(agent_key, ()))
        if build_task is not None:
            build_task.cancel()
            await asyncio.gather(build_task, return_exceptions=True)
        for run_task in run_tasks:
            run_task.cancel()
        if run_tasks:
            await asyncio.gather(*run_tasks, return_exceptions=True)
        if bundle is not None:
            bundle.session_service.clear()
            bundle.registry.clear()
        async with self._persistence_manager.advisory_lock(
            _conversation_lock_name(user_id, conversation_id),
            timeout=app_config.cfg.agent.orchestration.session_lock_timeout,
        ):
            await self._mark_conversation_deleted(user_id, conversation_id)
            await self._persistence_manager.delete_thread(
                get_thread_id(user_id, conversation_id)
            )

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
        bundle: AnalysisAgentBundle,
    ) -> AsyncIterator[PlannerTurnContext]:
        """登记完整用户回合并建立共享委派预算"""
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("Agent execution requires an asyncio task")
        agent_key = (user_id, conversation_id)
        async with self._state_lock:
            if agent_key in self._deleted_agent_keys:
                raise RuntimeError("Agent conversation has been deleted")
            self._run_tasks.setdefault(agent_key, set()).add(current_task)
        turn_context = PlannerTurnContext(
            user_id=user_id,
            conversation_id=conversation_id,
            planner_run_id=uuid4().hex,
            max_continuations=(app_config.cfg.agent.orchestration.max_continuations),
        )
        try:
            async with (
                bundle.planner_lock(),
                bundle.session_service.planner_run(turn_context.planner_run_id),
            ):
                if await bundle.conversation_deleted():
                    raise RuntimeError("Agent conversation has been deleted")
                yield turn_context
        finally:
            async with self._state_lock:
                tasks = self._run_tasks.get(agent_key)
                if tasks is not None:
                    tasks.discard(current_task)
                    if not tasks:
                        self._run_tasks.pop(agent_key, None)

    async def close(self) -> None:
        """释放 Agent 集合和未完成任务"""
        async with self._state_lock:
            build_tasks = list(self._build_tasks.values())
            run_tasks = [task for tasks in self._run_tasks.values() for task in tasks]
            bundles = list(self._bundles.values())
            self._build_tasks.clear()
            self._run_tasks.clear()
            self._bundles.clear()
            self._models = None
            self._planner_model_name = None
            self._tools = None
        for build_task in build_tasks:
            build_task.cancel()
        for run_task in run_tasks:
            run_task.cancel()
        pending_tasks = [*build_tasks, *run_tasks]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        for bundle in bundles:
            bundle.session_service.clear()
            bundle.registry.clear()


agent_manager = AgentManager(langgraph_postgres_manager)
