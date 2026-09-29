"""管理应用共享模型和工具，并装配每次 Run 使用的 Agent 图。"""

from contextlib import AsyncExitStack
from uuid import UUID

from deepagents import FilesystemMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

from app.assistant.agents.agent import create_agent
from app.assistant.agents.middleware.task_activity import TaskActivityMiddleware
from app.assistant.agents.specialists import build_specialists
from app.assistant.model_factory import create_configured_model
from app.assistant.resources import SYSTEM_PROMPTS
from app.assistant.services.tombstones import ConversationTombstoneStore
from app.assistant.services.types import build_planner_config, get_thread_id
from app.metadata.services.recall_handler import SemanticRecallHandler
from app.query.services.execution_handler import QueryExecutionHandler
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.shared.config import app_config
from app.shared.contracts.analysis import AGENT_TYPES, AgentType


class AgentManager:
    """管理共享模型、每次 Run 的图构造和会话图状态。"""

    def __init__(
        self,
        checkpointer: AsyncPostgresSaver,
        sandbox: DockerSandboxManager,
        tombstones: ConversationTombstoneStore,
        recall: SemanticRecallHandler,
        query: QueryExecutionHandler,
    ) -> None:
        self._checkpointer = checkpointer
        self._sandbox = sandbox
        self._tombstones = tombstones
        self._models: dict[str, BaseChatModel] = {}
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
        self._recall = recall
        self._query = query

    async def init(self) -> None:
        """初始化共享执行资源，失败时释放已创建的客户端。"""
        async with AsyncExitStack() as stack:
            models = {
                name: await stack.enter_async_context(create_configured_model(name))
                for name in self._model_names
            }
            self._models = models
            self._model_contexts = stack.pop_all()

    async def create_planner(
        self, user_id: int, conversation_id: UUID
    ) -> CompiledStateGraph:
        """准备会话执行资源并编译图。"""
        if await self._tombstones.exists(user_id, conversation_id):
            raise RuntimeError("该会话已被删除")
        backend = await self._sandbox.get_backend(user_id, conversation_id)
        return self._build_planner(user_id, conversation_id, backend)

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
        graph = self._build_planner(user_id, conversation_id)
        return await graph.aget_state(build_planner_config(user_id, conversation_id))

    async def delete_conversation_state(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """写入删除墓碑并清理会话 Checkpoint。"""
        # 先持久化墓碑再删除 Checkpoint，防止删除中的会话重新构建执行状态。
        await self._tombstones.save(user_id, conversation_id)
        thread_id = get_thread_id(user_id, conversation_id)
        await self._checkpointer.adelete_thread(thread_id)

    async def close(self) -> None:
        """释放共享模型客户端。"""
        self._models.clear()
        await self._model_contexts.aclose()

    def _build_planner(
        self,
        user_id: int,
        conversation_id: UUID,
        backend: DockerSandboxBackend | None = None,
    ) -> CompiledStateGraph:
        """编译 Planner 与 task 子 Agent，供状态读取或执行使用。"""
        if backend is None:
            backend = self._sandbox.graph_backend(user_id, conversation_id)
        return create_agent(
            name="planner",
            system_prompt=SYSTEM_PROMPTS["planner"],
            filesystem=FilesystemMiddleware(backend=backend, tools=["read_file"]),
            model=self._models[self._planner_model_name],
            tools=[],
            subagents=build_specialists(
                {
                    kind: self._models[name]
                    for kind, name in self._specialist_model_names.items()
                },
                backend,
                self._recall,
                self._query,
            ),
            middleware=[TaskActivityMiddleware()],
            sandbox=backend,
            checkpointer=self._checkpointer,
        )
