"""管理应用共享模型和工具，并装配每次 Run 使用的 Agent 图。"""

from contextlib import AsyncExitStack
from pathlib import PurePosixPath
from uuid import UUID

from deepagents import (
    FilesystemMiddleware,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.subagents import CompiledSubAgent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

from app.assistant.agents.context import build_planner_config, get_thread_id
from app.assistant.agents.filesystem import (
    agent_skills_mount_path,
    build_specialist_filesystem,
)
from app.assistant.agents.middleware.message_context import MessageContextMiddleware
from app.assistant.agents.middleware.task_activity import TaskActivityMiddleware
from app.assistant.agents.model_factory import create_configured_model
from app.assistant.agents.resources import ASSISTANT_RESOURCES_DIR, SYSTEM_PROMPTS
from app.assistant.agents.tools import create_shell_tool
from app.assistant.agents.tools.execute_sql import create_execute_sql_tool
from app.assistant.agents.tools.semantic_recall import create_semantic_recall_tool
from app.metadata.services.recall_handler import SemanticRecallHandler
from app.query.services.execution_handler import QueryExecutionHandler
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.paths import SandboxReadonlyMount
from app.shared.config import app_config
from app.shared.contracts.analysis import AGENT_TYPES, AgentType


class AgentManager:
    """管理共享模型、每次 Run 的图构造和会话图状态。"""

    def __init__(
        self,
        checkpointer: AsyncPostgresSaver,
        sandbox: DockerSandboxManager,
        recall: SemanticRecallHandler,
        query: QueryExecutionHandler,
    ) -> None:
        self._checkpointer = checkpointer
        self._sandbox = sandbox
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
            for model in models.values():
                model_params = dict(model._get_ls_params())  # pyright: ignore[reportPrivateUsage]
                register_harness_profile(
                    f"{model_params['ls_provider']}:{model_params['ls_model_name']}",
                    HarnessProfile(
                        general_purpose_subagent=GeneralPurposeSubagentProfile(
                            enabled=False
                        ),
                    ),
                )
            self._models = models
            self._model_contexts = stack.pop_all()

    async def create_planner(
        self, user_id: int, conversation_id: UUID
    ) -> CompiledStateGraph:
        """准备会话执行资源并编译图。"""
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
        graph = self._build_planner(user_id, conversation_id)
        return await graph.aget_state(build_planner_config(user_id, conversation_id))

    async def delete_conversation_state(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """由生命周期服务等待 Run 退出后清理会话 Checkpoint。"""
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
        return create_deep_agent(
            name="planner",
            system_prompt=SYSTEM_PROMPTS["planner"],
            model=self._models[self._planner_model_name],
            tools=[create_shell_tool(backend.shell_jobs)],
            subagents=self._build_specialists(backend),
            middleware=[
                FilesystemMiddleware(backend=backend, tools=["read_file"]),
                TaskActivityMiddleware(),
                MessageContextMiddleware(),
            ],
            backend=backend,
            checkpointer=self._checkpointer,
        )

    def _build_specialists(
        self, backend: DockerSandboxBackend
    ) -> list[CompiledSubAgent]:
        """构造无 Checkpoint 的专业图，共用当前会话工作区。"""
        analyst_skills = agent_skills_mount_path("analyst")
        explorer_filesystem = build_specialist_filesystem(backend)
        analyst_filesystem = build_specialist_filesystem(
            backend,
            skill_mount=SandboxReadonlyMount(
                source=ASSISTANT_RESOURCES_DIR / "analyst" / "skills",
                target=PurePosixPath(analyst_skills),
            ),
        )
        reviewer_filesystem = build_specialist_filesystem(backend)
        return [
            CompiledSubAgent(
                name="explorer",
                description="检索元数据、执行 SQL，取得可信数据并返回文件路径。",
                runnable=create_deep_agent(
                    name="explorer",
                    system_prompt=SYSTEM_PROMPTS["explorer"],
                    model=self._models[self._specialist_model_names["explorer"]],
                    tools=[
                        create_semantic_recall_tool(self._recall),
                        create_execute_sql_tool(self._query),
                        create_shell_tool(backend.shell_jobs),
                    ],
                    middleware=[explorer_filesystem, MessageContextMiddleware()],
                    backend=explorer_filesystem.backend,
                    checkpointer=False,
                ),
            ),
            CompiledSubAgent(
                name="analyst",
                description="基于数据文件进行分析、计算和可视化。",
                runnable=create_deep_agent(
                    name="analyst",
                    system_prompt=SYSTEM_PROMPTS["analyst"],
                    model=self._models[self._specialist_model_names["analyst"]],
                    tools=[create_shell_tool(backend.shell_jobs)],
                    middleware=[analyst_filesystem, MessageContextMiddleware()],
                    backend=analyst_filesystem.backend,
                    skills=[analyst_skills],
                    checkpointer=False,
                ),
            ),
            CompiledSubAgent(
                name="reviewer",
                description="检查分析口径、计算过程与交付物。",
                runnable=create_deep_agent(
                    name="reviewer",
                    system_prompt=SYSTEM_PROMPTS["reviewer"],
                    model=self._models[self._specialist_model_names["reviewer"]],
                    tools=[create_shell_tool(backend.shell_jobs)],
                    middleware=[reviewer_filesystem, MessageContextMiddleware()],
                    backend=reviewer_filesystem.backend,
                    checkpointer=False,
                ),
            ),
        ]
