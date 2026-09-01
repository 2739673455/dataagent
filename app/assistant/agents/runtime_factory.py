"""Conversation 级 Agent 运行时装配。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.contracts import (
    ConversationAgentRuntime,
    conversation_lifecycle_lock_name,
)
from app.assistant.agents.explorer.tools import (
    create_execute_sql_tool,
    delete_recalls,
    get_recall,
    list_recalls,
    merge_recalls,
    recall_context,
)
from app.assistant.agents.mcp import get_mcp_tools
from app.assistant.agents.planner.agent import create_planner_agent
from app.assistant.agents.planner.tools import (
    create_delegation_tool,
    create_delete_session_tool,
    create_list_sessions_tool,
)
from app.assistant.agents.session_service import AgentSessionService
from app.assistant.agents.session_store import PostgresSandboxSessionStore
from app.assistant.agents.specialists import (
    SpecialistAgentFactory,
    SpecialistDefinition,
    build_specialist_definitions,
)
from app.assistant.model_factory import create_configured_model
from app.assistant.services.conversation_tombstone import (
    ConversationTombstoneService,
)
from app.query.providers import build_query_execution_handler
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
)
from app.shared.config import app_config
from app.shared.contracts.analysis import AGENT_TYPES, AgentType


@dataclass(frozen=True, slots=True)
class _SharedAgentResources:
    """跨 Conversation 复用的模型和专业 Agent 定义。"""

    planner_model: BaseChatModel
    specialist_models: dict[AgentType, BaseChatModel]
    specialist_definitions: dict[AgentType, SpecialistDefinition]


class ConversationAgentRuntimeFactory:
    """初始化共享能力并装配 Conversation 级 Agent 运行时。"""

    def __init__(
        self,
        persistence: LangGraphPostgresManager,
        sandbox: DockerSandboxManager,
        tombstones: ConversationTombstoneService,
    ) -> None:
        """保存运行时依赖，模型和工具在首次使用时初始化。"""
        self._persistence = persistence
        self._sandbox = sandbox
        self._tombstones = tombstones
        self._init_lock = asyncio.Lock()
        self._resources: _SharedAgentResources | None = None

    async def init(self) -> None:
        """初始化所有 Conversation 共享的模型和专业 Agent 能力。"""
        if self._resources is not None:
            return
        async with self._init_lock:
            if self._resources is not None:
                return

            active_model_name = app_config.cfg.lm_config.active
            specialist_model_names: dict[AgentType, str] = {
                agent_type: (
                    active_model_name
                    if app_config.cfg.agent.specialists[agent_type].model == "default"
                    else app_config.cfg.agent.specialists[agent_type].model
                )
                for agent_type in AGENT_TYPES
            }
            configured_names = {active_model_name, *specialist_model_names.values()}
            models = {
                model_name: create_configured_model(model_name)
                for model_name in configured_names
            }
            specialist_models: dict[AgentType, BaseChatModel] = {
                agent_type: models[model_name]
                for agent_type, model_name in specialist_model_names.items()
            }
            explorer_tools = [
                recall_context,
                list_recalls,
                get_recall,
                merge_recalls,
                delete_recalls,
                create_execute_sql_tool(build_query_execution_handler(self._sandbox)),
            ]
            explorer_mcp_tools = await get_mcp_tools()

            self._resources = _SharedAgentResources(
                planner_model=models[active_model_name],
                specialist_models=specialist_models,
                specialist_definitions=build_specialist_definitions(
                    explorer_tools,
                    explorer_mcp_tools,
                ),
            )

    async def create(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        """装配一个隔离的 Conversation Agent 运行时。"""
        await self.init()
        resources = self._resources
        if resources is None:
            raise RuntimeError("Agent 运行时工厂尚未初始化")

        conversation_backend = await self._sandbox.get_backend(
            user_id,
            conversation_id,
        )
        checkpointer = self._persistence.get_checkpointer()
        orchestration = app_config.cfg.agent.orchestration
        session_store = PostgresSandboxSessionStore(
            user_id=user_id,
            conversation_id=conversation_id,
            persistence=self._persistence,
            checkpointer=checkpointer,
            sandbox=self._sandbox,
            conversation_backend=conversation_backend,
        )
        specialist_factory = SpecialistAgentFactory(
            resources.specialist_definitions,
            resources.specialist_models,
            self._sandbox,
            checkpointer,
        )
        session_service = AgentSessionService(
            build_agent=specialist_factory.create,
            session_store=session_store,
            user_id=user_id,
            conversation_id=conversation_id,
            max_parallel_sessions=orchestration.max_parallel_sessions,
        )
        planner = self._create_planner(
            resources.planner_model,
            session_service,
            conversation_backend,
            checkpointer,
        )
        return ConversationAgentRuntime(
            planner=planner,
            session_service=session_service,
            planner_lock=lambda: self._persistence.advisory_lock(
                conversation_lifecycle_lock_name(user_id, conversation_id),
            ),
            conversation_deleted=lambda: self._tombstones.exists(
                user_id,
                conversation_id,
            ),
        )

    def _create_planner(
        self,
        model: BaseChatModel,
        session_service: AgentSessionService,
        backend: DockerSandboxBackend,
        checkpointer: BaseCheckpointSaver,
    ) -> CompiledStateGraph:
        """构建绑定 Session 生命周期工具的 Planner。"""
        planner_tools = [
            create_delegation_tool(session_service),
            create_list_sessions_tool(session_service),
            create_delete_session_tool(session_service),
        ]
        interpreter = app_config.cfg.agent.interpreter
        return create_planner_agent(
            model=model,
            tools=planner_tools,
            backend=backend,
            checkpointer=checkpointer,
            session_service=session_service,
            interpreter_memory_limit_bytes=interpreter.memory_limit_bytes,
        )

    def close(self) -> None:
        """释放当前进程持有的共享 Agent 配置。"""
        self._resources = None
