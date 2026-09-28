"""管理应用共享模型和工具，并装配每次 Run 使用的 Agent 图。"""

from contextlib import AsyncExitStack
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

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
    """模型与 MCP 工具随应用初始化，Agent 图按 Run 创建。"""

    def __init__(
        self,
        persistence: LangGraphPostgresManager,
        sandbox: DockerSandboxManager,
        recall: SemanticRecallHandler,
        query: QueryExecutionHandler,
    ) -> None:
        self._persistence = persistence
        self._sandbox = sandbox
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
        self._planner_model_name = active
        self._specialist_model_names = names
        self._definitions = build_specialist_definitions(
            [create_semantic_recall_tool(recall), create_execute_sql_tool(query)]
        )

    def specialists(self) -> SpecialistAgentFactory:
        """装配专业图工厂，绑定当前 Checkpointer。"""
        return SpecialistAgentFactory(
            self._definitions,
            {
                kind: self._models[name]
                for kind, name in self._specialist_model_names.items()
            },
            self._sandbox,
            self._persistence.get_checkpointer(),
            self._mcp_tools,
        )

    async def init(self) -> None:
        """初始化共享执行资源，失败时释放已创建的客户端。"""
        async with AsyncExitStack() as stack:
            models = {
                name: await stack.enter_async_context(create_configured_model(name))
                for name in self._model_names
            }
            mcp_tools = await get_mcp_tools()
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
        session_service = AgentSessionService(
            agents=specialist_factory,
            persistence=self._persistence,
            sandbox=self._sandbox,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        planner = create_planner_agent(
            model=self._models[self._planner_model_name],
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
        backend = await self._sandbox.get_backend(user_id, conversation_id)
        return self.build(user_id, conversation_id, backend)

    async def close(self) -> None:
        """释放共享模型客户端。"""
        self._models.clear()
        self._mcp_tools.clear()
        await self._model_contexts.aclose()
