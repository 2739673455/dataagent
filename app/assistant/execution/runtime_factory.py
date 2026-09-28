"""编译 Conversation 图，并按执行需要初始化模型、MCP 和沙箱。"""

import asyncio
from contextlib import AsyncExitStack
from typing import cast
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.model_profile import ModelProfile
from langchain_core.tools import BaseTool

from app.assistant.agents.deferred import DeferredChatModel
from app.assistant.agents.explorer.tools import (
    create_execute_sql_tool,
    create_semantic_recall_tool,
)
from app.assistant.agents.mcp import get_mcp_tools
from app.assistant.agents.planner.agent import create_planner_agent
from app.assistant.agents.planner.tools import (
    create_delegation_tool,
    create_delete_session_tool,
    create_list_sessions_tool,
)
from app.assistant.agents.specialists import (
    SpecialistAgentFactory,
    build_specialist_definitions,
)
from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.session_store import PostgresSandboxSessionStore
from app.assistant.execution.types import ConversationAgentRuntime
from app.assistant.model_factory import create_configured_model
from app.metadata.services.recall_handler import SemanticRecallHandler
from app.query.services.execution_handler import QueryExecutionHandler
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.config import app_config
from app.shared.contracts.analysis import AGENT_TYPES, AgentType


class ConversationAgentRuntimeFactory:
    """图结构使用延迟资源引用，执行资源由 init/create 持有。"""

    def __init__(
        self,
        persistence: LangGraphPostgresManager,
        sandbox: DockerSandboxManager,
        recall: SemanticRecallHandler,
        query: QueryExecutionHandler,
    ) -> None:
        self._persistence = persistence
        self._sandbox = sandbox
        self._init_lock = asyncio.Lock()
        self._models: dict[str, BaseChatModel] = {}
        self._mcp_tools: list[BaseTool] = []
        self._model_contexts = AsyncExitStack()
        active = app_config.cfg.lm_config.active
        names: dict[AgentType, str] = {
            kind: (
                active
                if app_config.cfg.agent.specialists[kind].model == "default"
                else app_config.cfg.agent.specialists[kind].model
            )
            for kind in AGENT_TYPES
        }
        self._model_names = {active, *names.values()}
        deferred = {}
        for name in self._model_names:
            config = app_config.cfg.lm_config.models[name]
            deferred[name] = DeferredChatModel(
                resolve=lambda name=name: self._models[name],
                profile=cast(
                    ModelProfile,
                    {
                        **config.profile.model_dump(),
                        "image_tool_message": config.api_protocol == "responses"
                        and config.profile.image_inputs,
                    },
                ),
            )
        self._planner_model = deferred[active]
        self._explorer_tools = [
            create_semantic_recall_tool(recall),
            create_execute_sql_tool(query),
        ]
        self._definitions = build_specialist_definitions(self._explorer_tools, [])
        self._specialist_models: dict[AgentType, BaseChatModel] = {
            kind: deferred[name] for kind, name in names.items()
        }

    def specialists(self) -> SpecialistAgentFactory:
        """装配专业图工厂，绑定当前 Checkpointer。"""
        return SpecialistAgentFactory(
            self._definitions,
            self._specialist_models,
            self._sandbox,
            self._persistence.get_checkpointer(),
            self.init,
            lambda: self._mcp_tools,
        )

    async def init(self) -> None:
        """初始化共享执行资源，失败时释放已创建的客户端。"""
        async with self._init_lock:
            if self._models:
                return
            async with AsyncExitStack() as stack:
                models = {
                    name: await stack.enter_async_context(create_configured_model(name))
                    for name in self._model_names
                }
                mcp_tools = await get_mcp_tools()
                build_specialist_definitions(self._explorer_tools, mcp_tools)
                self._models = models
                self._mcp_tools = mcp_tools
                self._model_contexts = stack.pop_all()

    def build(
        self,
        user_id: int,
        conversation_id: UUID,
        backend: DockerSandboxBackend | None = None,
    ) -> ConversationAgentRuntime:
        """编译 Planner 与 Session 工具，供状态读取或执行使用。"""
        checkpointer = self._persistence.get_checkpointer()
        specialist_factory = self.specialists()
        session_store = PostgresSandboxSessionStore(
            user_id=user_id,
            conversation_id=conversation_id,
            persistence=self._persistence,
            checkpointer=checkpointer,
            sandbox=self._sandbox,
            build_agent=specialist_factory.build,
        )
        orchestration = app_config.cfg.agent.orchestration
        session_service = AgentSessionService(
            build_agent=specialist_factory.create,
            session_store=session_store,
            user_id=user_id,
            conversation_id=conversation_id,
            max_parallel_sessions=orchestration.max_parallel_sessions,
            max_sessions=orchestration.max_sessions,
        )

        planner = create_planner_agent(
            model=self._planner_model,
            tools=[
                create_delegation_tool(session_service),
                create_list_sessions_tool(session_service),
                create_delete_session_tool(session_service),
            ],
            backend=backend
            if backend is not None
            else self._sandbox.graph_backend(user_id, conversation_id),
            checkpointer=checkpointer,
        )
        return ConversationAgentRuntime(
            planner=planner, session_service=session_service
        )

    async def create(
        self, user_id: int, conversation_id: UUID
    ) -> ConversationAgentRuntime:
        """准备会话执行资源并编译图。"""
        await self.init()
        backend = await self._sandbox.get_backend(user_id, conversation_id)
        return self.build(user_id, conversation_id, backend)

    async def close(self) -> None:
        """释放共享模型客户端。"""
        self._models.clear()
        self._mcp_tools.clear()
        await self._model_contexts.aclose()
